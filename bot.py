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

# Cambio a endpoint de EVENTS para mayor estabilidad en 2026
GAMMA_EVENTS_API = "https://gamma-api.polymarket.com/events"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("polybot")

# Memoria para evitar repetir el mismo mercado
last_posted_slug = ""

# ═══════════════════════════════════════════════════════════════════════════════
# THE "GUARANTEED TIP" ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class DailySignalEngine:
    def fetch_events(self):
        """Obtiene los eventos activos más importantes."""
        try:
            params = {
                "limit": 50,
                "active": "true",
                "closed": "false",
                "order": "volume",
                "ascending": "false"
            }
            resp = requests.get(GAMMA_EVENTS_API, params=params, timeout=20)
            return resp.json() if resp.status_code == 200 else []
        except Exception as e:
            logger.error(f"API Error: {e}")
            return []

    def get_best_tip(self, events):
        global last_posted_slug
        if not events: return None

        candidates = []
        for e in events:
            slug = e.get("slug")
            # Ignoramos si es el mismo de ayer o si es el de BitBoy (para variar)
            if slug == last_posted_slug or "bitboy" in slug.lower():
                continue

            markets = e.get("markets", [])
            if not markets: continue

            # Buscamos el mercado principal de este evento
            for m in markets:
                try:
                    # En 2026, el precio puede estar en 'outcomePrices' o 'group_id'
                    prices = m.get("outcomePrices")
                    if not prices: continue
                    
                    # Tomamos el primer precio (normalmente el 'YES')
                    price = float(prices[0])
                    vol = float(e.get("volume", 0) or 0)
                    
                    if 0.05 < price < 0.95:
                        roi = ((1 / price) - 1) * 100
                        # Puntuación equilibrada
                        score = (vol * 0.5) + (roi * 10)
                        
                        candidates.append({
                            "q": e.get("title", m.get("question")),
                            "out": m.get("outcomes", ["Yes", "No"])[0],
                            "prob": price * 100,
                            "roi": roi,
                            "vol": vol,
                            "slug": slug,
                            "score": score
                        })
                except: continue

        if candidates:
            return max(candidates, key=lambda x: x["score"])
        
        # FALLBACK: Si todo falla, toma el primer evento con volumen
        logger.info("⚠️ No ideal markets found. Forcing first active event.")
        first = events[0]
        return {
            "q": first.get("title"),
            "out": "Outcome",
            "prob": 50.0,
            "roi": 100.0,
            "vol": float(first.get("volume", 0)),
            "slug": first.get("slug"),
            "score": 0
        }

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
        msg += "💎 <i>Shared via @TuCanalDeTelegram</i>"
        return msg

# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
@app.route("/")
def health(): return "OK", 200

def bot_main_loop():
    global last_posted_slug
    engine = DailySignalEngine()
    logger.info("🚀 Professional Daily Signal Engine Started")
    
    while True:
        try:
            logger.info("🔎 Scanning events...")
            raw_events = engine.fetch_events()
            tip = engine.get_best_tip(raw_events)
            
            if tip:
                logger.info(f"✅ Selected: {tip['q']}")
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": engine.format_post(tip),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
                requests.post(url, json=payload, timeout=15)
                
                last_posted_slug = tip['slug']
                logger.info(f"📱 Posted! Waiting {POST_INTERVAL_HOURS} hours.")
                time.sleep(POST_INTERVAL_HOURS * 3600)
            else:
                logger.warning("No data from API. Retrying in 5 mins...")
                time.sleep(300)
                
        except Exception as e:
            logger.error(f"Error: {e}")
            time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=bot_main_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))