"""
==============================================================================
  POLYMARKET SMART MONEY TIPSTER — FINAL PRODUCTION VERSION
  ─────────────────────────────────────────────────────────
  FIXES: 
  - Layer 3 API: Changed 'CURRENT' to 'REALIZEDPNL' to fix 400 Errors.
  - UI: All Telegram alerts converted to English.
  - Render: Optimized port binding and heartbeat for Free Plan.
==============================================================================
"""

import os
import sys
import json
import time
import logging
import threading
import requests
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from flask import Flask
from dotenv import load_dotenv

load_dotenv()

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
USER_BANKROLL = float(os.getenv("USER_BANKROLL", "1000"))

BASE_DATA_API = "https://data-api.polymarket.com"
BASE_GAMMA_API = "https://gamma-api.polymarket.com"

MARKET_SCANNER = {
    "enabled": True,
    "min_probability": 65,
    "max_probability": 92,
    "min_volume": 10000,
    "min_liquidity": 5000,
    "markets_per_post": 5,
    "scan_interval_hours": 6,
}

SMART_MONEY = {
    "enabled": True,
    "leaderboard_refresh_hours": 6,
    "trade_poll_seconds": 60,
    "activity_lookback_seconds": 3600, # 1 hour for Render Free resilience
    "min_pnl_all_time": 5000,
    "min_win_rate": 0.54,
    "min_closed_positions": 8,
    "min_trade_size_usd": 20, 
    "min_market_liquidity": 5000,
    "max_probability": 0.92,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("polybot")

# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SmartWallet:
    address: str
    username: str
    pnl_all: float = 0.0
    win_rate: float = 0.0
    tier: str = "B"
    last_seen_trade_ts: int = 0

# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        logger.error(f"Telegram failed: {e}")

def api_get(url, params=None):
    try:
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code == 200: return resp.json()
    except: return None
    return None

# ═══════════════════════════════════════════════════════════════════════════════
# ENGINES
# ═══════════════════════════════════════════════════════════════════════════════

class MarketScanner:
    def run_cycle(self):
        logger.info("[Scanner] Fetching markets...")
        raw = api_get(f"{BASE_GAMMA_API}/markets", {"limit": 100, "active": "true", "closed": "false"})
        if not raw: return
        
        best = []
        for m in raw:
            try:
                vol = float(m.get("volume") or 0)
                liq = float(m.get("liquidity") or 0)
                if vol < MARKET_SCANNER["min_volume"] or liq < MARKET_SCANNER["min_liquidity"]: continue
                
                tokens = m.get("tokens", [])
                bt = max(tokens, key=lambda t: float(t.get("price") or 0))
                price = float(bt.get("price"))
                prob = price * 100
                
                if MARKET_SCANNER["min_probability"] <= prob <= MARKET_SCANNER["max_probability"]:
                    best.append({
                        "q": m["question"], "out": bt["outcome"], "prob": prob, 
                        "roi": ((1/price)-1)*100, "slug": m["slug"]
                    })
            except: continue

        if best:
            msg = "📊 <b>DAILY MARKET SCAN</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            for m in best[:5]:
                msg += f"🔹 <b>{m['q']}</b>\nSide: {m['out']} ({m['prob']:.1f}%)\nROI: +{m['roi']:.1f}%\n<a href='https://polymarket.com/event/{m['slug']}'>Trade Link</a>\n\n"
            send_telegram(msg)

class SmartMoneyTracker:
    def __init__(self):
        self.tracked_wallets = {}

    def refresh_wallets(self):
        logger.info("[SmartMoney] Layer 3: Validating track records...")
        entries = api_get(f"{BASE_DATA_API}/v1/leaderboard", {"timePeriod": "ALL", "orderBy": "PNL", "limit": 100})
        if not entries: return

        valid = {}
        for entry in entries:
            addr = entry.get("proxyWallet")
            if not addr: continue
            
            # THE FIX: Using REALIZEDPNL to prevent 400 error
            closed = api_get(f"{BASE_DATA_API}/closed-positions", {
                "user": addr, "limit": 100, "sortBy": "REALIZEDPNL", "sortDirection": "DESC"
            })
            if not closed or len(closed) < SMART_MONEY["min_closed_positions"]: continue
            
            wins = sum(1 for p in closed if float(p.get("cashPnl") or 0) > 0)
            wr = wins / len(closed)
            
            if wr >= SMART_MONEY["min_win_rate"]:
                valid[addr] = SmartWallet(address=addr, username=entry.get("userName") or addr[:10], win_rate=wr)
                logger.info(f" ✓ Verified: {entry.get('userName')} ({wr:.0%})")
            time.sleep(0.1)
        self.tracked_wallets = valid

    def check_trades(self):
        lookback = int(time.time()) - SMART_MONEY["activity_lookback_seconds"]
        for addr, sw in self.tracked_wallets.items():
            trades = api_get(f"{BASE_DATA_API}/activity", {"user": addr, "type": "TRADE", "side": "BUY", "start": lookback})
            if not trades: continue
            for t in trades:
                ts = int(t.get("timestamp") or 0)
                if ts <= sw.last_seen_trade_ts: continue
                sw.last_seen_trade_ts = ts
                
                usd = float(t.get("size", 0)) * float(t.get("price", 0))
                if usd < SMART_MONEY["min_trade_size_usd"]: continue
                
                msg = (
                    f"🔥 <b>SMART MONEY ALERT</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>{sw.username}</b> (WR: {sw.win_rate:.0%})\n"
                    f"💰 Bet: <b>${usd:,.0f}</b> on <i>{t.get('outcome')}</i>\n"
                    f"❓ {t.get('title')}\n\n"
                    f"🔗 <a href='https://polymarket.com/event/{t.get('slug')}'>View Market</a>"
                )
                send_telegram(msg)

# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
@app.route("/")
def health(): return "OK", 200

def bot_loop():
    scanner = MarketScanner()
    tracker = SmartMoneyTracker()
    last_scan = 0
    last_refresh = 0
    
    while True:
        now = time.time()
        if now - last_scan > MARKET_SCANNER["scan_interval_hours"] * 3600:
            scanner.run_cycle(); last_scan = now
        
        if now - last_refresh > SMART_MONEY["leaderboard_refresh_hours"] * 3600:
            tracker.refresh_wallets(); last_refresh = now
            
        tracker.check_trades()
        time.sleep(SMART_MONEY["trade_poll_seconds"])

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)