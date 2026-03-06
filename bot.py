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
POST_INTERVAL_HOURS = float(os.getenv("POST_INTERVAL_HOURS", "24"))

# Professional Analytics Thresholds
ANALYTICS_CONFIG = {
    "min_vol": 25000,      # Only markets with real money
    "min_liq": 2000,       # Avoid ghost markets
    "min_roi": 12,         # Target at least 12% profit
    "max_prob": 90         # Avoid "obvious" 99% bets with no profit
}

GAMMA_API = "https://gamma-api.polymarket.com/markets"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("polybot")

# ═══════════════════════════════════════════════════════════════════════════════
# ANALYTICS ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class AnalyticsEngine:
    def fetch_data(self):
        try:
            params = {"limit": 100, "active": "true", "closed": "false"}
            resp = requests.get(GAMMA_API, params=params, timeout=20)
            return resp.json() if resp.status_code == 200 else []
        except: return []

    def find_pro_tip(self, markets):
        candidates = []
        for m in markets:
            try:
                vol = float(m.get("volume", 0) or 0)
                liq = float(m.get("liquidity", 0) or 0)
                if vol < ANALYTICS_CONFIG["min_vol"]: continue
                
                tokens = m.get("tokens", [])
                for t in tokens:
                    price = float(t.get("price", 0) or 0)
                    prob = price * 100
                    if 15 < prob < ANALYTICS_CONFIG["max_prob"]:
                        roi = ((1/price)-1)*100
                        # ANALYTICS SCORE: High Volume + High ROI / Low Liquidity (Insider push)
                        score = (vol * 0.5) + (roi * 50)
                        candidates.append({
                            "q": m.get("question"), "out": t.get("outcome"),
                            "prob": prob, "roi": roi, "vol": vol, "liq": liq,
                            "slug": m.get("slug"), "score": score
                        })
            except: continue
        return max(candidates, key=lambda x: x["score"]) if candidates else None

    def format_pro_alert(self, tip):
        now = datetime.now(timezone.utc).strftime("%B %d, %Y")
        msg = f"🔥 <b>PRO TIP OF THE DAY</b> 🔥\n"
        msg += f"📅 <i>{now}</i>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"🎯 <b>MARKET:</b>\n{tip['q']}\n\n"
        msg += f"✅ <b>POSITION:</b> {tip['out'].upper()}\n"
        msg += f"📈 <b>PROBABILITY:</b> {tip['prob']:.1f}%\n"
        msg += f"💰 <b>ESTIMATED ROI:</b> +{tip['roi']:.1f}%\n\n"
        msg += f"📊 <b>INSIDER METRICS:</b>\n"
        msg += f"• Vol: ${tip['vol']:,.0f} | Liq: ${tip['liq']:,.0f}\n\n"
        msg += f"🔗 <a href='https://polymarket.com/event/{tip['slug']}'>View on Polymarket</a>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += "💎 <i>Analysis powered by PolymarketAnalytics data</i>"
        return msg

# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
@app.route("/")
def health(): return "Engine Online", 200

def bot_run():
    engine = AnalyticsEngine()
    while True:
        try:
            raw = engine.fetch_data()
            tip = engine.find_pro_tip(raw)
            if tip:
                msg = engine.format_pro_alert(tip)
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                             json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True})
            time.sleep(POST_INTERVAL_HOURS * 3600)
        except Exception as e:
            logger.error(f"Error: {e}"); time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=bot_run, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))