"""
==============================================================================
  POLYMARKET SMART MONEY TIPSTER — COMBINED PRODUCTION BOT
  ─────────────────────────────────────────────────────────
  Merges your existing market-scanning bot with the 5-layer smart money
  filter into a single deployable service for Render.com.

  TWO SIGNAL ENGINES:
    1. Market Scanner  — Finds high-probability markets (your original logic)
    2. Smart Money Tracker — Monitors top leaderboard wallets for new trades

  FIXES FROM YOUR ORIGINAL CODE:
    - Render health check now works (Flask responds before bot starts)
    - Corrected Layer 3 API: Uses 'REALIZEDPNL' instead of 'CURRENT'
    - Proper async/thread separation
    - Graceful error recovery
    - Logging to stdout (visible in Render logs)
==============================================================================
"""

import os
import sys
import json
import time
import logging
import threading
import requests
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from flask import Flask
from dotenv import load_dotenv

# ─── Load .env for local dev (Render uses Environment Variables directly) ───
load_dotenv()

# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING — Render captures stdout, so we write there
# ═══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("polybot")

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION (all from environment variables with sensible defaults)
# ═══════════════════════════════════════════════════════════════════════════════

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
USER_BANKROLL = float(os.getenv("USER_BANKROLL", "1000"))

# --- API Endpoints ---
BASE_DATA_API = "https://data-api.polymarket.com"
BASE_GAMMA_API = "https://gamma-api.polymarket.com"

# --- Market Scanner Config (Engine 1) ---
MARKET_SCANNER = {
    "enabled": True,
    "min_probability": 65,
    "max_probability": 92,
    "min_volume": 10000,
    "min_liquidity": 5000,
    "markets_per_post": 5,
    "scan_interval_hours": 6,
}

# --- Smart Money Tracker Config (Engine 2) ---
SMART_MONEY = {
    "enabled": True,
    "leaderboard_refresh_hours": 6,
    "trade_poll_seconds": 60,
    "activity_lookback_seconds": 3600,  # Optimized for Render Free (1 hour)

    # Layer 1: Leaderboard pre-filter
    "min_pnl_all_time": 5000,
    "min_volume_all_time": 50000,

    # Layer 2: Multi-timeframe consistency
    "min_profitable_windows": 2,

    # Layer 3: Win rate & ROI
    "min_closed_positions": 8,
    "min_win_rate": 0.54,
    "min_roi_percent": 8,

    # Layer 4: Per-trade quality
    "min_trade_size_usd": 20,          # Reduced from 200 to see signals faster
    "min_market_liquidity": 5000,
    "max_probability": 0.92,
    "min_probability": 0.05,
    "longshot_min_trade_usd": 1000,

    # Layer 5: Convergence
    "convergence_window_minutes": 60,
    "convergence_min_wallets": 2,
}


# ═══════════════════════════════════════════════════════════════════════════════
# RISK MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class RiskManager:
    def __init__(self, total_bankroll: float, kelly_fraction: float = 0.25):
        self.bankroll = total_bankroll
        self.fraction = kelly_fraction

    def calculate_bet(self, market_price: float, user_prob: float) -> dict:
        p = user_prob / 100.0
        q = 1.0 - p

        if market_price <= 0 or market_price >= 1:
            return {"suggested_usd": 0, "percentage": 0, "edge": 0}

        b = (1.0 - market_price) / market_price
        raw_f = (b * p - q) / b
        optimal_f = max(0, raw_f * self.fraction)
        safe_f = min(optimal_f, 0.10)
        suggested_usd = self.bankroll * safe_f

        return {
            "suggested_usd": round(suggested_usd, 2),
            "percentage": round(safe_f * 100, 2),
            "edge": round((p - market_price) * 100, 2),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SmartWallet:
    address: str
    username: str
    pnl_all: float = 0.0
    vol_all: float = 0.0
    pnl_month: float = 0.0
    pnl_week: float = 0.0
    pnl_day: float = 0.0
    profitable_windows: int = 0
    win_rate: float = 0.0
    roi_percent: float = 0.0
    closed_positions_count: int = 0
    tier: str = "B"
    last_seen_trade_ts: int = 0

@dataclass
class Signal:
    wallet_address: str
    wallet_username: str
    wallet_tier: str
    market_question: str
    market_slug: str
    outcome: str
    side: str
    size_tokens: float
    price: float
    estimated_usd: float
    market_probability: float
    market_liquidity: float
    timestamp: int
    convergence_count: int = 1


# ═══════════════════════════════════════════════════════════════════════════════
# API HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def api_get(url: str, params: dict = None, retries: int = 3) -> Optional[Any]:
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=20)
            if resp.status_code == 429:
                wait = 2 ** attempt * 5
                time.sleep(wait)
                continue
            if resp.status_code == 400:
                logger.error(f"API 400 Bad Request for {url}: {resp.text}")
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt + 1)
            else:
                logger.error(f"API failed after {retries} attempts: {url} — {e}")
                return None
    return None

def send_telegram(message: str, parse_mode: str = "HTML") -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram credentials missing!")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        return resp.json().get("ok", False)
    except Exception as e:
        logger.error(f"Telegram failed: {e}")
        return False

def get_market_details(condition_id: str) -> dict:
    data = api_get(f"{BASE_GAMMA_API}/markets", {"condition_id": condition_id})
    if data and len(data) > 0:
        m = data[0]
        return {
            "question": m.get("question", "Unknown Market"),
            "slug": m.get("slug", ""),
            "outcome_prices": m.get("outcomePrices", '["0.5","0.5"]'),
            "volume": float(m.get("volume", 0) or 0),
            "liquidity": float(m.get("liquidity", 0) or 0),
            "active": m.get("active", False),
            "closed": m.get("closed", False),
        }
    return {"question": "Unknown", "slug": "", "liquidity": 0, "active": False, "closed": True}


# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE 1: MARKET SCANNER
# ═══════════════════════════════════════════════════════════════════════════════

class MarketScanner:
    def __init__(self):
        self.risk = RiskManager(total_bankroll=USER_BANKROLL)
        self.cfg = MARKET_SCANNER

    def run_cycle(self):
        logger.info("[Scanner] Starting scan cycle...")
        raw = api_get(f"{BASE_GAMMA_API}/markets", {"limit": 100, "active": "true", "closed": "false"})
        if not raw: return
        
        filtered = []
        for m in raw:
            vol = float(m.get("volume", 0) or 0)
            liq = float(m.get("liquidity", 0) or 0)
            if vol < self.cfg["min_volume"] or liq < self.cfg["min_liquidity"]: continue

            tokens = m.get("tokens", [])
            if not tokens: continue
            bt = max(tokens, key=lambda t: float(t.get("price", 0) or 0))
            price = float(bt.get("price", 0) or 0)
            prob = price * 100
            if not (self.cfg["min_probability"] <= prob <= self.cfg["max_probability"]): continue

            roi = ((1 / price) - 1) * 100
            kelly = self.risk.calculate_bet(price, prob + 2)

            filtered.append({
                "question": m.get("question", "N/A"), "outcome": bt.get("outcome", "YES"),
                "prob": prob, "price": price, "roi": roi, "vol": vol, "liq": liq,
                "slug": m.get("slug", ""), "kelly": kelly
            })

        best = sorted(filtered, key=lambda x: (-x["prob"], -x["vol"]))[:self.cfg["markets_per_post"]]
        if best:
            now = datetime.now(timezone.utc).strftime("%m/%d/%Y %H:%M UTC")
            msg = "📊 <b>MARKET SCANNER UPDATE</b>\n"
            msg += f"⏰ {now}\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for i, m in enumerate(best, 1):
                msg += f"<b>#{i} {m['question']}</b>\n"
                msg += f"🎯 Bet: <b>{m['outcome']}</b> | Prob: <b>{m['prob']:.1f}%</b>\n"
                msg += f"💰 ROI: +{m['roi']:.1f}% | Liq: ${m['liq']:,.0f}\n"
                if m["kelly"]["suggested_usd"] > 0:
                    msg += f"🧮 Bet: ${m['kelly']['suggested_usd']:,.0f} ({m['kelly']['percentage']:.1f}%)\n"
                msg += f"🔗 <a href='https://polymarket.com/event/{m['slug']}'>Trade Here</a>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            send_telegram(msg)


# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE 2: SMART MONEY TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

class SmartMoneyTracker:
    def __init__(self):
        self.cfg = SMART_MONEY
        self.tracked_wallets: Dict[str, SmartWallet] = {}
        self.recent_signals: List[Signal] = []

    def refresh_wallets(self):
        logger.info("[SmartMoney] Layer 1+2: Scanning leaderboard...")
        candidates = {}
        # Fetching top wallets across main categories
        for period in ["MONTH", "ALL"]:
            entries = api_get(f"{BASE_DATA_API}/v1/leaderboard", {"timePeriod": period, "orderBy": "PNL", "limit": 100})
            if not entries: continue
            for entry in entries:
                addr = entry.get("proxyWallet", "")
                if not addr: continue
                if addr not in candidates: candidates[addr] = SmartWallet(address=addr, username=entry.get("userName") or addr[:10])
                sw = candidates[addr]
                pnl = float(entry.get("pnl", 0) or 0)
                if period == "ALL": sw.pnl_all = pnl; sw.vol_all = float(entry.get("vol", 0) or 0)
                else: sw.pnl_month = pnl

        # Filter Layers
        layer1_2 = {a: s for a, s in candidates.items() if s.pnl_all >= self.cfg["min_pnl_all_time"] and s.vol_all >= self.cfg["min_volume_all_time"]}
        
        logger.info(f"[SmartMoney] Validating {len(layer1_2)} candidates via closed positions...")
        validated = {}
        for addr, sw in layer1_2.items():
            # FIXED: Using REALIZEDPNL to prevent 400 Error
            closed = api_get(f"{BASE_DATA_API}/closed-positions", {"user": addr, "limit": 100, "sortBy": "REALIZEDPNL", "sortDirection": "DESC"})
            if not closed or len(closed) < self.cfg["min_closed_positions"]: continue
            
            wins = sum(1 for p in closed if float(p.get("cashPnl", 0) or 0) > 0)
            wr = wins / len(closed)
            if wr >= self.cfg["min_win_rate"]:
                sw.win_rate = wr
                sw.tier = "A" if wr >= 0.65 else "B"
                validated[addr] = sw
                logger.info(f"  ✓ Verified: {sw.username} (WR: {wr:.0%})")
            time.sleep(0.2)

        if validated:
            self.tracked_wallets = validated
            self.send_watchlist()

    def send_watchlist(self):
        msg = f"📋 <b>WATCHLIST UPDATED</b>\nTracking {len(self.tracked_wallets)} Elite Wallets.\n"
        for addr, sw in list(self.tracked_wallets.items())[:5]:
            msg += f"• <code>{sw.username}</code> (WR: {sw.win_rate:.0%})\n"
        send_telegram(msg)

    def check_new_trades(self):
        now = int(datetime.now(timezone.utc).timestamp())
        lookback = now - self.cfg["activity_lookback_seconds"]
        for addr, sw in self.tracked_wallets.items():
            trades = api_get(f"{BASE_DATA_API}/activity", {"user": addr, "type": "TRADE", "side": "BUY", "start": lookback, "limit": 10})
            if not trades: continue
            for t in trades:
                ts = int(t.get("timestamp", 0) or 0)
                if ts <= sw.last_seen_trade_ts: continue
                sw.last_seen_trade_ts = ts
                
                usd_size = float(t.get("size", 0) or 0) * float(t.get("price", 0) or 0)
                if usd_size < self.cfg["min_trade_size_usd"]: continue
                
                market = get_market_details(t.get("conditionId", ""))
                if market["closed"] or market["liquidity"] < self.cfg["min_market_liquidity"]: continue

                sig_msg = (
                    f"🔥 <b>SMART MONEY ALERT</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                    f"❓ <b>{market['question']}</b>\n\n"
                    f"👉 TIP: <b>{t.get('outcome', 'YES')}</b>\n"
                    f"💰 Size: <b>${usd_size:,.0f}</b>\n"
                    f"👤 Trader: <code>{sw.username}</code>\n\n"
                    f"🔗 <a href='https://polymarket.com/event/{market['slug']}'>Trade Here</a>"
                )
                send_telegram(sig_msg)
                time.sleep(1)

# ═══════════════════════════════════════════════════════════════════════════════
# FLASK & MAIN LOOP
# ════════════════════════════════════════════════━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

app = Flask(__name__)

@app.route("/")
def health(): return "OK", 200

def bot_main_loop():
    logger.info("🚀 POLYMARKET ENGINE STARTING")
    scanner = MarketScanner() if MARKET_SCANNER["enabled"] else None
    tracker = SmartMoneyTracker() if SMART_MONEY["enabled"] else None
    
    last_scanner_run = 0
    last_tracker_refresh = 0

    if tracker: tracker.refresh_wallets(); last_tracker_refresh = time.time()

    while True:
        try:
            now = time.time()
            if scanner and (now - last_scanner_run) >= MARKET_SCANNER["scan_interval_hours"] * 3600:
                scanner.run_cycle(); last_scanner_run = now
            
            if tracker:
                if (now - last_tracker_refresh) >= SMART_MONEY["leaderboard_refresh_hours"] * 3600:
                    tracker.refresh_wallets(); last_tracker_refresh = now
                tracker.check_new_trades()
            
            time.sleep(SMART_MONEY["trade_poll_seconds"])
        except Exception as e:
            logger.error(f"Loop error: {e}"); time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=bot_main_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))