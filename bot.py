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
# This is now used as a "Goal". The bot will check every 15 mins if it needs to post.
POST_INTERVAL_HOURS = float(os.getenv("POST_INTERVAL_HOURS", "24"))

GAMMA_API = "https://gamma-api.polymarket.com/events"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("polybot")

# PERSISTENT-LIKE MEMORY (Resets on restart, but we check timing)
last_post_time = 0 
posted_history = []

# ═══════════════════════════════════════════════════════════════════════════════
# RELIABLE SELECTION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class RandomSignalEngine:
    def fetch_pool(self):
        try:
            params = {"limit": 50, "active": "true", "closed": "false", "order": "volume", "ascending": "false"}
            resp = requests.get(GAMMA_API, params=params, timeout=15)
            return resp.json() if resp.status_code == 200 else []
        except: return []

    def get_tip(self, events):
        global posted_history
        if not events: return None
        
        # Filter pool for uniqueness
        available = [e for e in events if e.get("slug") not in posted_history]
        if not available:
            posted_history = []
            available = events

        selected_event = random.choice(available)
        markets = selected_event.get("markets", [])
        if not markets: return None

        m = markets[0]
        try:
            # Robust price parsing
            prices_raw = m.get("outcomePrices", [0.5, 0.5])
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
            
            # Outcome cleaning
            outcomes_raw = m.get("outcomes", ["Yes", "No"])
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw

            price = float(prices[0])
            # Ensure price is valid for ROI calculation
            if price <= 0 or price >= 1: price = 0.5 

            return {
                "q": selected_event.get("title", m.get("question")),
                "out": str(outcomes[0]),
                "prob": price * 100,
                "roi": ((1/price)-1)*100,
                "vol": float(selected_event.get("volume", 0) or 0),
                "slug": selected_event.get("slug")
            }
        except Exception as e:
            logger.error(f"Selection error: {e}")
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
# EXECUTION LOOP
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)

@app.route("/")
def health(): return "Bot Running", 200

def bot_main_loop():
    global last_post_time, posted_history
    engine = RandomSignalEngine()
    
    while True:
        try:
            current_time = time.time()
            elapsed_time = current_time - last_post_time
            
            # Check if it's time to post (or if we've never posted since restart)
            if elapsed_time >= (POST_INTERVAL_HOURS * 3600) or last_post_time == 0:
                logger.info("🔎 Time for a new post. Scanning...")
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
                        last_post_time = time.time()
                        posted_history.append(tip['slug'])
                        logger.info(f"📱 Post Successful: {tip['q']}")
                    else:
                        logger.error(f"Telegram error: {resp.status_code}. Retrying in 10m.")
                        time.sleep(600)
                        continue
                else:
                    logger.warning("No market found. Retrying in 10m.")
                    time.sleep(600)
                    continue
            
            # Short sleep (15 mins) before checking the timer again
            # This keeps the Render instance active with UptimeRobot
            time.sleep(900) 
                
        except Exception as e:
            logger.error(f"Loop error: {e}")
            time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=bot_main_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)