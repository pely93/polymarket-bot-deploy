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
# Set this to how often you want the daily tip (e.g., 24 for once a day)
POST_INTERVAL_HOURS = float(os.getenv("POST_INTERVAL_HOURS", "24"))

# APIs
GAMMA_API = "https://gamma-api.polymarket.com/markets"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("polybot")

# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class DailyTipEngine:
    def fetch_all_active_markets(self):
        """Fetches a wide range of active markets."""
        try:
            params = {"limit": 100, "active": "true", "closed": "false", "unrequested": "false"}
            resp = requests.get(GAMMA_API, params=params, timeout=20)
            return resp.json() if resp.status_code == 200 else []
        except Exception as e:
            logger.error(f"Fetch error: {e}")
            return []

    def select_best_tip(self, markets):
        """Scores markets to find the absolute best one to share."""
        scored_markets = []
        for m in markets:
            try:
                # Basic requirements: Must have price and volume
                tokens = m.get("tokens", [])
                if len(tokens) < 2: continue
                
                vol = float(m.get("volume", 0) or 0)
                liq = float(m.get("liquidity", 0) or 0)
                
                # Pick the outcome with the best value (Price between 0.20 and 0.85)
                # We avoid 0.99 (no profit) and 0.01 (too unlikely)
                for t in tokens:
                    price = float(t.get("price", 0) or 0)
                    if 0.15 < price < 0.85:
                        roi = ((1 / price) - 1) * 100
                        # Score = Volume weighted by ROI
                        score = (vol * 0.4) + (liq * 0.6) + (roi * 10)
                        
                        scored_markets.append({
                            "question": m.get("question"),
                            "outcome": t.get("outcome"),
                            "price": price,
                            "prob": price * 100,
                            "roi": roi,
                            "vol": vol,
                            "liq": liq,
                            "slug": m.get("slug"),
                            "score": score
                        })
            except: continue
        
        if not scored_markets: return None
        # Return the market with the highest score
        return max(scored_markets, key=lambda x: x["score"])

    def format_message(self, tip):
        """Creates the English post for Telegram."""
        now = datetime.now(timezone.utc).strftime("%B %d, %Y")
        
        msg = f"🌟 <b>POLYMARKET: TIP OF THE DAY</b> 🌟\n"
        msg += f"📅 <i>{now}</i>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"🎯 <b>MARKET:</b>\n{tip['question']}\n\n"
        msg += f"✅ <b>RECOMMENDED BET:</b> {tip['outcome'].upper()}\n"
        msg += f"📈 <b>PROBABILITY:</b> {tip['prob']:.1f}%\n"
        msg += f"💰 <b>POTENTIAL ROI:</b> +{tip['roi']:.1f}%\n\n"
        msg += f"📊 <b>MARKET STATS:</b>\n"
        msg += f"• Volume: ${tip['vol']:,.0f}\n"
        msg += f"• Liquidity: ${tip['liq']:,.0f}\n\n"
        msg += f"🔗 <a href='https://polymarket.com/event/{tip['slug']}'>TRADE ON POLYMARKET</a>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += "💎 <i>Shared via Polymarket Analytics Bot</i>"
        return msg

# ═══════════════════════════════════════════════════════════════════════════════
# SERVER & LOOP
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)

@app.route("/")
def health(): return "Bot Active", 200

def bot_main_loop():
    engine = DailyTipEngine()
    logger.info("🚀 Daily Tip Engine Started")
    
    while True:
        try:
            logger.info("Scanning for the best daily tip...")
            raw_markets = engine.fetch_all_active_markets()
            best_tip = engine.select_best_tip(raw_markets)
            
            if best_tip:
                message = engine.format_message(best_tip)
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
                requests.post(url, json=payload, timeout=15)
                logger.info(f"Daily tip posted: {best_tip['question']}")
            else:
                logger.warning("No suitable markets found today.")

            # Wait for the next interval
            time.sleep(POST_INTERVAL_HOURS * 3600)
            
        except Exception as e:
            logger.error(f"Loop error: {e}")
            time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=bot_main_loop, daemon=True).start()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)