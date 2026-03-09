import os
import sys
import time
import logging
import threading
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
# Set to 24 for one tip per day, or 12 for two tips per day
POST_INTERVAL_HOURS = float(os.getenv("POST_INTERVAL_HOURS", "24"))

GAMMA_API = "https://gamma-api.polymarket.com/markets"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("polybot")

# ═══════════════════════════════════════════════════════════════════════════════
# ANALYTICS & SCORING ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class ProAnalyticsEngine:
    def fetch_markets(self):
        try:
            params = {"limit": 100, "active": "true", "closed": "false"}
            resp = requests.get(GAMMA_API, params=params, timeout=20)
            return resp.json() if resp.status_code == 200 else []
        except: return []

    def get_best_daily_signal(self, markets):
        """Scores all markets and picks the absolute best one to share."""
        scored_list = []
        for m in markets:
            try:
                vol = float(m.get("volume", 0) or 0)
                liq = float(m.get("liquidity", 0) or 0)
                tokens = m.get("tokens", [])
                
                for t in tokens:
                    price = float(t.get("price", 0) or 0)
                    if 0.10 < price < 0.90:  # Avoid near-certain or impossible bets
                        roi = ((1 / price) - 1) * 100
                        # Professional Score: Volume weight (40%) + Liquidity (40%) + ROI (20%)
                        score = (vol * 0.4) + (liq * 0.4) + (roi * 5)
                        
                        scored_list.append({
                            "q": m.get("question"), "out": t.get("outcome"),
                            "prob": price * 100, "roi": roi, "vol": vol, "liq": liq,
                            "slug": m.get("slug"), "score": score
                        })
            except: continue
        
        # Guaranteed to return the highest score if any markets exist
        return max(scored_list, key=lambda x: x["score"]) if scored_list else None

    def format_message(self, tip):
        now = datetime.now(timezone.utc).strftime("%B %d, %Y")
        msg = f"🏆 <b>POLYMARKET: DAILY TOP PICK</b> 🏆\n"
        msg += f"📅 <i>{now}</i>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"🎯 <b>MARKET:</b>\n{tip['q']}\n\n"
        msg += f"✅ <b>RECOMMENDED:</b> {tip['out'].upper()}\n"
        msg += f"📈 <b>PROBABILITY:</b> {tip['prob']:.1f}%\n"
        msg += f"💰 <b>POTENTIAL ROI:</b> +{tip['roi']:.1f}%\n\n"
        msg += f"📊 <b>MARKET STRENGTH:</b>\n"
        msg += f"• 24h Volume: ${tip['vol']:,.0f}\n"
        msg += f"• Liquidity: ${tip['liq']:,.0f}\n\n"
        msg += f"🔗 <a href='https://polymarket.com/event/{tip['slug']}'>Open in Polymarket</a>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += "💎 <i>Selection based on Volume/Liquidity analysis</i>"
        return msg

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
@app.route("/")
def health(): return "Engine Online", 200

def bot_main_loop():
    engine = ProAnalyticsEngine()
    logger.info("🚀 Professional Daily Signal Engine Started")
    
    while True:
        try:
            logger.info("Scanning for the daily top pick...")
            raw = engine.fetch_markets()
            best_tip = engine.get_best_daily_signal(raw)
            
            if best_tip:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": engine.format_message(best_tip),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
                resp = requests.post(url, json=payload, timeout=15)
                
                if resp.status_code == 200:
                    logger.info(f"✅ Success: Daily signal posted for {best_tip['q']}")
                    # SUCCESS: Now wait 24 hours for the next one
                    time.sleep(POST_INTERVAL_HOURS * 3600)
                else:
                    logger.error(f"❌ Telegram Error: {resp.text}")
                    time.sleep(300) # Wait 5 mins and try again if Telegram failed
            else:
                logger.warning("⚠️ No suitable markets found. Retrying in 10 minutes...")
                time.sleep(600) # If no market found, don't sleep 24h, just wait 10 mins
                
        except Exception as e:
            logger.error(f"💥 Critical Loop error: {e}")
            time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=bot_main_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))