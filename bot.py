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

GAMMA_API = "https://gamma-api.polymarket.com/markets"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("polybot")

# Global variable to remember the last 3 topics to avoid repeats
# Usamos una lista para dar más variedad
last_posted_slugs = []

# ═══════════════════════════════════════════════════════════════════════════════
# ANALYTICS ENGINE (WITH ANTI-REPEAT LOGIC)
# ═══════════════════════════════════════════════════════════════════════════════

class ProAnalyticsEngine:
    def fetch_markets(self):
        try:
            params = {"limit": 100, "active": "true", "closed": "false", "order": "volume", "ascending": "false"}
            resp = requests.get(GAMMA_API, params=params, timeout=20)
            return resp.json() if resp.status_code == 200 else []
        except: return []

    def get_best_daily_signal(self, markets):
        scored_list = []
        if not markets: return None

        for m in markets:
            try:
                slug = m.get("slug")
                # ANTI-REPEAT: Skip if this market was recently posted
                if slug in last_posted_slugs:
                    continue

                vol = float(m.get("volume", 0) or 0)
                liq = float(m.get("liquidity", 0) or 0)
                tokens = m.get("tokens", [])
                
                if not tokens: continue
                
                for t in tokens:
                    price = float(t.get("price", 0) or 0)
                    if 0.05 < price < 0.95:
                        roi = ((1 / price) - 1) * 100
                        # Score: Prioritizamos Volumen y ROI
                        score = (vol * 0.1) + (roi * 10)
                        
                        scored_list.append({
                            "q": m.get("question"), "out": t.get("outcome"),
                            "prob": price * 100, "roi": roi, "vol": vol, "liq": liq,
                            "slug": slug, "score": score
                        })
            except: continue
        
        # Si nada pasa el filtro o todo está repetido, forzamos el mejor de la lista original que no esté en memoria
        if not scored_list:
            logger.info("⚠️ High-score markets already posted or not found. Looking for alternatives...")
            for m in markets:
                if m.get("slug") not in last_posted_slugs:
                    tokens = m.get("tokens", [])
                    if tokens:
                        t = tokens[0]
                        price = float(t.get("price", 0.5))
                        return {
                            "q": m.get("question"), "out": t.get("outcome"),
                            "prob": price * 100, "roi": ((1/price)-1)*100 if price > 0 else 0,
                            "vol": float(m.get("volume", 0)), "liq": float(m.get("liquidity", 0)),
                            "slug": m.get("slug"), "score": 0
                        }
        
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
        msg += "💎 <i>Shared via Polymarket Tipster</i>"
        return msg

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
@app.route("/")
def health(): return "OK", 200

def bot_main_loop():
    global last_posted_slugs
    engine = ProAnalyticsEngine()
    logger.info("🚀 Professional Daily Signal Engine Started")
    
    while True:
        try:
            logger.info("🔎 Scanning for a fresh daily pick...")
            raw = engine.fetch_markets()
            best_tip = engine.get_best_daily_signal(raw)
            
            if best_tip:
                logger.info(f"✅ New Tip selected: {best_tip['q']}")
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": engine.format_message(best_tip),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
                resp = requests.post(url, json=payload, timeout=15)
                
                if resp.status_code == 200:
                    # Remember this post to avoid repeats
                    last_posted_slugs.append(best_tip['slug'])
                    # Mantener solo los últimos 5 para no llenar la memoria
                    if len(last_posted_slugs) > 5:
                        last_posted_slugs.pop(0)
                        
                    logger.info(f"📱 Message sent! History: {last_posted_slugs}")
                    time.sleep(POST_INTERVAL_HOURS * 3600)
                else:
                    logger.error(f"❌ Telegram Error: {resp.text}")
                    time.sleep(300)
            else:
                logger.warning("⚠️ No new markets found. Retrying in 10 mins...")
                time.sleep(600)
                
        except Exception as e:
            logger.error(f"💥 Error: {e}")
            time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=bot_main_loop, daemon=True).start()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)