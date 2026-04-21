import os
import time
import logging
import threading
import random
import json
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

class FinalSignalEngine:
    def fetch_pool(self):
        try:
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

        # Sort by highest probability to pick the "safest" signal (The Favorite)
        try:
            def get_top_price(m):
                p_raw = m.get("outcomePrices", "[0.5, 0.5]")
                p = json.loads(p_raw) if isinstance(p_raw, str) else p_raw
                return max(float(p[0]), float(p[1]))
            markets.sort(key=get_top_price, reverse=True)
        except: pass

        m = markets[0]
        
        try:
            prices_raw = m.get("outcomePrices", [0.5, 0.5])
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
            
            outcomes_raw = m.get("outcomes", ["Yes", "No"])
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw

            # Logic to decide if we recommend YES or NO based on higher probability
            # Usually signals follow the highest probability (The "Winner")
            price_yes = float(prices[0])
            price_no = float(prices[1])
            
            if price_yes >= price_no:
                chosen_side = "YES"
                chosen_prob = price_yes
                raw_name = str(outcomes[0])
            else:
                chosen_side = "NO"
                chosen_prob = price_no
                raw_name = str(outcomes[0]) # We still keep the candidate name as the ID

            # Name Cleaning
            market_label = m.get("groupItemTitle") or m.get("question", "")
            if raw_name.upper() in ["YES", "NO"]:
                position_display = f"{market_label.replace('?', '').strip()} [{chosen_side}]"
            else:
                position_display = f"{raw_name.upper()} [{chosen_side}]"

            # ROI Calculation for the chosen side
            roi = ((1 / chosen_prob) - 1) * 100 if chosen_prob > 0 else 0

            return {
                "q": event_title,
                "out": position_display,
                "prob": chosen_prob * 100,
                "roi": roi,
                "vol": float(selected_event.get("volume", 0) or 0),
                "slug": selected_event.get("slug")
            }
        except Exception as e:
            logger.error(f"Error: {e}")
            return None

    def format_post(self, tip):
        now = datetime.now(timezone.utc).strftime("%B %d, %Y")
        msg = f"🏆 <b>POLYMARKET: DAILY TOP PICK</b> 🏆\n"
        msg += f"📅 <i>{now}</i>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"🎯 <b>MARKET:</b>\n{tip['q']}\n\n"
        msg += f"✅ <b>POSITION:</b> {tip['out']}\n"
        msg += f"📈 <b>PROBABILITY:</b> {tip['prob']:.1f}%\n"
        msg += f"💰 <b>POTENTIAL ROI:</b> +{tip['roi']:.1f}%\n\n"
        msg += f"📊 <b>STATS:</b> Vol ${tip['vol']:,.0f}\n\n"
        msg += f"🔗 <a href='https://polymarket.com/event/{tip['slug']}'>Trade on Polymarket</a>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += "💎 <b>Shared via @polymsignals</b>"
        return msg

# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
@app.route("/")
def health(): return "LIVE", 200

def bot_main_loop():
    engine = FinalSignalEngine()
    time.sleep(10)
    while True:
        try:
            pool = engine.fetch_pool()
            tip = engine.get_tip(pool)
            if tip:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {"chat_id": TELEGRAM_CHAT_ID, "text": engine.format_post(tip), "parse_mode": "HTML", "disable_web_page_preview": True}
                if requests.post(url, json=payload).status_code == 200:
                    time.sleep(POST_INTERVAL_HOURS * 3600)
                else: time.sleep(300)
            else: time.sleep(300)
        except: time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=bot_main_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))