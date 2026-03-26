import os
import sys
import time
import logging
import threading
import random
import requests
from datetime import datetime, timezone, timedelta
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

# Global memory to prevent repeats across restarts (if possible)
last_posted_slug = ""

# ═══════════════════════════════════════════════════════════════════════════════
# DIVERSITY ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class DiversityEngine:
    def fetch_data(self):
        try:
            # We fetch 100 markets to have a large pool for variety
            params = {"limit": 100, "active": "true", "closed": "false", "order": "volume", "ascending": "false"}
            resp = requests.get(GAMMA_API, params=params, timeout=20)
            return resp.json() if resp.status_code == 200 else []
        except: return []

    def select_unique_tip(self, events):
        global last_posted_slug
        candidates = []
        
        # Keywords to IGNORE (The ones that get stuck at the top)
        blacklist = ["2028", "presidential nominee", "convicted", "bitboy"]

        for e in events:
            slug = e.get("slug", "")
            title = e.get("title", "").lower()
            
            # 1. Skip if it's the same as the last post
            if slug == last_posted_slug: continue
            
            # 2. Skip if it's in the blacklist
            if any(word in title for word in blacklist): continue

            markets = e.get("markets", [])
            if not markets: continue

            for m in markets:
                try:
                    prices = m.get("outcomePrices")
                    if not prices: continue
                    
                    price = float(prices[0])
                    vol = float(e.get("volume", 0) or 0)
                    
                    # We want markets with real action but decent ROI
                    if 0.10 < price < 0.85:
                        roi = ((1 / price) - 1) * 100
                        # Score: Prioritize Volume, but add a random factor for variety
                        score = (vol * 0.7) + (roi * 5)
                        
                        candidates.append({
                            "q": e.get("title"), "out": m.get("outcomes", ["Yes", "No"])[0],
                            "prob": price * 100, "roi": roi, "vol": vol,
                            "slug": slug, "score": score
                        })
                except: continue

        if not candidates: return None

        # 3. SORT & SHUFFLE: Take the top 10 best and pick a RANDOM one
        candidates = sorted(candidates, key=lambda x: x["score"], reverse=True)
        top_pool = candidates[:10] 
        return random.choice(top_pool)

    def format_message(self, tip):
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
# EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
@app.route("/")
def health(): return "Engine Online", 200

def bot_main_loop():
    global last_posted_slug
    engine = DiversityEngine()
    
    # Wait for Render to settle
    time.sleep(30)

    while True:
        try:
            raw = engine.fetch_data()
            tip = engine.select_unique_tip(raw)
            
            if tip:
                logger.info(f"✅ Selected new market: {tip['q']}")
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": engine.format_message(tip),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
                requests.post(url, json=payload, timeout=15)
                
                last_posted_slug = tip['slug']
                # Sleep for 24 hours (or your config)
                time.sleep(POST_INTERVAL_HOURS * 3600)
            else:
                logger.warning("No new unique markets found. Retrying in 1 hour.")
                time.sleep(3600)
                
        except Exception as e:
            logger.error(f"Error: {e}"); time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=bot_main_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))