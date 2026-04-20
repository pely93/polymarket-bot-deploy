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

# memory resets on Render restarts, so we use a shorter logic
posted_history = []

class SmartSignalEngine:
    def fetch_pool(self):
        try:
            params = {"limit": 80, "active": "true", "closed": "false", "order": "volume", "ascending": "false"}
            resp = requests.get(GAMMA_API, params=params, timeout=15)
            return resp.json() if resp.status_code == 200 else []
        except: return []

    def extract_deadline(self, text):
        """Attempts to find a date like 'June 30' or 'May 1' in the question."""
        months = r"(January|February|March|April|May|June|July|August|September|October|November|December)"
        match = re.search(f"by {months} \d+", text, re.IGNORECASE)
        return match.group(0).replace("by ", "") if match else None

    def get_tip(self, events):
        global posted_history
        if not events: return None
        
        available = [e for e in events if e.get("slug") not in posted_history]
        if not available:
            posted_history = []
            available = events

        selected_event = random.choice(available)
        markets = selected_event.get("markets", [])
        if not markets: return None

        m = markets[0]
        try:
            prices_raw = m.get("outcomePrices", [0.5, 0.5])
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
            
            outcomes_raw = m.get("outcomes", ["Yes", "No"])
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw

            price = float(prices[0])
            if price <= 0.01 or price >= 0.99: price = 0.5 

            question = selected_event.get("title", m.get("question"))
            deadline = self.extract_deadline(question)
            
            # Formatting the position to include the date context
            pos_text = str(outcomes[0]).upper()
            if deadline:
                pos_text = f"{pos_text} (Target: {deadline})"

            return {
                "q": question,
                "out": pos_text,
                "prob": price * 100,
                "roi": ((1/price)-1)*100,
                "vol": float(selected_event.get("volume", 0) or 0),
                "slug": selected_event.get("slug")
            }
        except: return None

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
# MAIN SERVER & LOOP
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
@app.route("/")
def health(): return "ONLINE", 200

def bot_main_loop():
    engine = SmartSignalEngine()
    time.sleep(10)

    while True:
        try:
            logger.info("🔎 Looking for today's market...")
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
                    logger.info(f"📱 Posted: {tip['q']}")
                    # Wait 24 hours after a success
                    time.sleep(POST_INTERVAL_HOURS * 3600)
                else:
                    logger.error("Failed to send. Retrying in 5m.")
                    time.sleep(300)
            else:
                logger.warning("No unique market found. Retrying in 5m.")
                time.sleep(300)
                
        except Exception as e:
            logger.error(f"Error: {e}")
            time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=bot_main_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)