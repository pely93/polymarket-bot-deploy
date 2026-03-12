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
# Lo ideal es ponerlo en 24, pero si quieres más actividad puedes bajarlo a 12
POST_INTERVAL_HOURS = float(os.getenv("POST_INTERVAL_HOURS", "24"))

GAMMA_API = "https://gamma-api.polymarket.com/markets"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("polybot")

# Memoria temporal (Se limpia al reiniciar, lo cual es bueno para forzar posts)
last_posted_slugs = []

# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE: DAILY SIGNAL (MAX ACTIVITY MODE)
# ═══════════════════════════════════════════════════════════════════════════════

class DailySignalEngine:
    def fetch_markets(self):
        """Intenta obtener los 100 mercados con más volumen."""
        try:
            params = {"limit": 100, "active": "true", "closed": "false", "order": "volume", "ascending": "false"}
            resp = requests.get(GAMMA_API, params=params, timeout=20)
            return resp.json() if resp.status_code == 200 else []
        except Exception as e:
            logger.error(f"Error API: {e}")
            return []

    def select_best_tip(self, markets):
        """Busca el mejor mercado que no esté en la lista de repetidos."""
        candidates = []
        for m in markets:
            slug = m.get("slug")
            if not slug or slug in last_posted_slugs:
                continue

            tokens = m.get("tokens", [])
            if not tokens: continue
            
            # Buscamos el token con precio más razonable (evitamos 0.99 o 0.01)
            for t in tokens:
                try:
                    price = float(t.get("price", 0) or 0)
                    if 0.05 < price < 0.95:
                        vol = float(m.get("volume", 0) or 0)
                        roi = ((1 / price) - 1) * 100
                        # Puntuación: Volumen + un extra por ROI
                        score = vol + (roi * 10)
                        
                        candidates.append({
                            "q": m["question"], "out": t.get("outcome", "YES"),
                            "prob": price * 100, "roi": roi, "vol": vol,
                            "slug": slug, "score": score
                        })
                except: continue

        # Ordenar por puntuación y devolver el mejor
        if candidates:
            return max(candidates, key=lambda x: x["score"])
        
        # SI NO HAY CANDIDATOS NUEVOS: Forzar el primer mercado con volumen que no sea BitBoy
        logger.info("⚠️ Forzando selección para evitar canal vacío...")
        for m in markets:
            if "bitboy" not in m.get("slug", "").lower():
                tokens = m.get("tokens", [])
                if tokens:
                    t = tokens[0]
                    price = float(t.get("price", 0.5))
                    return {
                        "q": m.get("question"), "out": t.get("outcome", "YES"),
                        "prob": price * 100, "roi": ((1/price)-1)*100 if price > 0 else 100,
                        "vol": float(m.get("volume", 0)), "slug": m.get("slug"), "score": 0
                    }
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
        msg += "💎 <i>Selection based on Whale Activity</i>"
        return msg

# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
@app.route("/")
def health(): return "OK", 200

def bot_main_loop():
    global last_posted_slugs
    engine = DailySignalEngine()
    logger.info("🚀 Bot Loop Started")
    
    while True:
        try:
            logger.info("🔎 Scanning for a tip...")
            raw_markets = engine.fetch_markets()
            tip = engine.select_best_tip(raw_markets)
            
            if tip:
                logger.info(f"✅ Sending Tip: {tip['q']}")
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": engine.format_post(tip),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
                requests.post(url, json=payload, timeout=15)
                
                # Guardar en memoria y esperar ciclo largo
                last_posted_slugs.append(tip['slug'])
                if len(last_posted_slugs) > 10: last_posted_slugs.pop(0)
                
                logger.info(f"📱 Posted! Waiting {POST_INTERVAL_HOURS} hours.")
                time.sleep(POST_INTERVAL_HOURS * 3600)
            else:
                logger.warning("No market found. Retrying in 5 minutes...")
                time.sleep(300)
                
        except Exception as e:
            logger.error(f"Error: {e}")
            time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=bot_main_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))