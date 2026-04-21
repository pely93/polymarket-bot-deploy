import os
import time
import logging
import threading
import random
import json
import re
import requests
from datetime import datetime, timezone
from flask import Flask
from dotenv import load_dotenv

load_dotenv()

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POST_INTERVAL_HOURS = float(os.getenv("POST_INTERVAL_HOURS", "24"))

GAMMA_API = "https://gamma-api.polymarket.com/events"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("polybot")

posted_history = []

class SmartSignalEngine:
    def fetch_pool(self):
        try:
            # We fetch more events to ensure we have a good random variety
            params = {"limit": 100, "active": "true", "closed": "false", "order": "volume", "ascending": "false"}
            resp = requests.get(GAMMA_API, params=params, timeout=15)
            return resp.json() if resp.status_code == 200 else []
        except: return []

    def get_tip(self, events):
        global posted_history
        if not events: return None
        
        available = [e for e in events if e.get("slug") not in posted_history]
        if not available:
            posted_history = []
            available = events

        selected_event = random.choice(available)
        event_title = selected_event.get("title", "Unknown Market")
        markets = selected_event.get("markets", [])
        if not markets: return None

        # SORT MARKETS BY PRICE (To find the "Favorite" or most likely outcome)
        try:
            def get_market_price(m):
                p_raw = m.get("outcomePrices", "[0.5, 0.5]")
                p = json.loads(p_raw) if isinstance(p_raw, str) else p_raw
                return float(p[0]) if p else 0
            
            markets.sort(key=get_market_price, reverse=True)
        except: pass

        m = markets[0] # Pick the top market (the one with 94%, etc.)
        
        try:
            # 1. Parse Prices
            prices_raw = m.get("outcomePrices", [0.5, 0.5])
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
            price = float(prices[0])

            # 2. Parse Outcome Name
            outcomes_raw = m.get("outcomes", ["Yes", "No"])
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
            raw_position = str(outcomes[0])

            # 3. MULTI-OPTION FIX:
            # If the outcome is just "Yes", use the specific Market Title (Candidate Name)
            market_title = m.get("groupItemTitle") or m.get("question", "")
            
            if raw_position.upper() == "YES":
                # Clean up the market title (remove question marks if present)
                final_position = market_title.replace("?", "").strip()
            else:
                final_position = raw_position

            return {
                "q": event_title,
                "out": final_position,
                "prob": price * 100,
                "roi": ((1/price)-1)*100,
                "vol": float(selected_event.get("volume", 0) or 0),
                "slug": selected_event.get("slug")
            }
        except Exception as e:
            logger.error(f"Error parsing market: {e}")
            return None

    def format_post(self, tip):
        now = datetime.now(timezone.utc).strftime("%B %d, %Y")
        msg = f"🏆 <b>POLYMARKET: DAILY TOP PICK</b> 🏆\n"
        msg += f"📅 <i>{now}</i>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"🎯 <b>MARKET:</b>\n{tip['q']}\n\n"
        msg += f"✅ <b>POSITION:</b> {tip['out'].upper()}\n"
        msg += f"📈 <b>PROBABILITY:</b> {tip['prob']:.1f}%\n"
        msg += f"💰 <b>POTENTIAL ROI:</b> +{tip['roi']:.1f}%\n\n"
        msg += f"📊 <b>STATS:</b> Vol ${tip['vol']:,.0f}\n\n"
        msg += f"🔗 <a href='https://polymarket.com/event/{tip['slug']}'>Trade on Polymarket</a>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += "💎 <b>Shared via @polymsignals</b>"
        return msg

# ═══════════════════════════════════════════════════════════════════════════════
# WEB SERVER & LOOP
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
@app.route("/")
def health(): return "OK", 200

def bot_main_loop():
    engine = SmartSignalEngine()
    time.sleep(10)

    while True:
        try:
            logger.info("🎲 Choosing market...")
            pool = engine.fetch_pool()
            tip = engine.get_tip(pool)
            
            if tip:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": engine.format_post(tip),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
                resp = requests.post(url, json=payload, timeout=15)
                
                if resp.status_code == 200:
                    logger.info("📱 Post sent.")
                    time.sleep(POST_INTERVAL_HOURS * 3600)
                else:
                    time.sleep(300)
            else:
                time.sleep(300)
        except Exception as e:
            logger.error(f"Loop error: {e}")
            time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=bot_main_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)