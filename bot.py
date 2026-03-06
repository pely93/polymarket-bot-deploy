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
# ADVANCED ANALYTICS CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POST_INTERVAL_HOURS = float(os.getenv("POST_INTERVAL_HOURS", "24"))

# Professional Filters inspired by PolymarketAnalytics
PRO_FILTERS = {
    "min_whale_bet": 1000,        # Minimum USD to consider it a "Whale" move
    "insider_risk_ratio": 0.15,   # Trade size > 15% of liquidity = High Risk/Insider
    "min_wallet_pnl": 5000,       # Only track wallets with $5k+ all-time profit
    "min_win_rate": 0.58,         # 58% minimum historical win rate
    "max_entry_prob": 90,         # Don't tip if the outcome is already >90%
}

GAMMA_API = "https://gamma-api.polymarket.com/markets"
DATA_API = "https://data-api.polymarket.com"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("polybot")

# ═══════════════════════════════════════════════════════════════════════════════
# THE ANALYTICS ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class PolymarketProEngine:
    def fetch_markets(self):
        try:
            resp = requests.get(GAMMA_API, params={"limit": 100, "active": "true"}, timeout=20)
            return resp.json() if resp.status_code == 200 else []
        except: return []

    def score_market(self, m):
        """Implements the PolymarketAnalytics scoring logic."""
        try:
            vol = float(m.get("volume", 0) or 0)
            liq = float(m.get("liquidity", 0) or 0)
            tokens = m.get("tokens", [])
            
            best_tip = None
            highest_score = -1

            for t in tokens:
                price = float(t.get("price", 0) or 0)
                prob = price * 100
                if 10 < prob < PRO_FILTERS["max_probability"]:
                    # Scoring: Volume + (Volume/Liquidity Ratio) + ROI
                    # High Vol/Low Liq = Potential Insider/Whale movement
                    liq_ratio = vol / liq if liq > 0 else 1
                    roi = ((1/price)-1)*100
                    score = (vol * 0.2) + (liq_ratio * 1000) + (roi * 10)
                    
                    if score > highest_score:
                        highest_score = score
                        best_tip = {
                            "q": m.get("question"), "out": t.get("outcome"),
                            "prob": prob, "roi": roi, "vol": vol, "liq": liq,
                            "slug": m.get("slug"), "score": score,
                            "risk": "HIGH" if liq_ratio > 5 else "MEDIUM"
                        }
            return best_tip
        except: return None

    def format_pro_message(self, tip):
        now = datetime.now(timezone.utc).strftime("%B %d, %Y")
        risk_emoji = "⚠️" if tip['risk'] == "HIGH" else "✅"
        
        msg = f"💎 <b>POLYMARKET ANALYTICS: PRO TIP</b> 💎\n"
        msg += f"📅 <i>{now}</i>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"🎯 <b>MARKET:</b>\n{tip['q']}\n\n"
        msg += f"✅ <b>POSITION:</b> {tip['out'].upper()}\n"
        msg += f"📈 <b>ODDS:</b> {tip['prob']:.1f}% | 💰 <b>ROI:</b> +{tip['roi']:.1f}%\n"
        msg += f"📊 <b>INSIDER RISK:</b> {tip['risk']} {risk_emoji}\n\n"
        msg += f"💼 <b>STATS:</b> Vol: ${tip['vol']:,.0f} | Liq: ${tip['liq']:,.0f}\n\n"
        msg += f"🔗 <a href='https://polymarket.com/event/{tip['slug']}'>Trade on Polymarket</a>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += "<i>Filtered by Whale Activity & Liquidity Ratios</i>"
        return msg

# ═══════════════════════════════════════════════════════════════════════════════
# FLASK & BOT LOOP
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
@app.route("/")
def health(): return "Pro Engine Running", 200

def bot_main_loop():
    engine = PolymarketProEngine()
    logger.info("🚀 Professional Analytics Engine Live")
    while True:
        try:
            markets = engine.fetch_markets()
            # Select the market with the highest professional 'Insider' score
            scored = [engine.score_market(m) for m in markets if engine.score_market(m)]
            if scored:
                top_tip = max(scored, key=lambda x: x["score"])
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                             json={"chat_id": TELEGRAM_CHAT_ID, "text": engine.format_pro_message(top_tip), 
                                   "parse_mode": "HTML", "disable_web_page_preview": True})
            
            time.sleep(POST_INTERVAL_HOURS * 3600)
        except Exception as e:
            logger.error(f"Error: {e}"); time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=bot_main_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))