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

# Memoria de los últimos mercados para evitar repeticiones (Se limpia al reiniciar)
last_posted_slug = ""

# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE: ULTRA-DIVERSITY SCANNER
# ═══════════════════════════════════════════════════════════════════════════════

class DailySignalEngine:
    def fetch_markets(self):
        """Intenta obtener mercados con volumen real, sin filtros de búsqueda agresivos."""
        try:
            # Traemos más mercados (100) para tener de donde elegir
            params = {"limit": 100, "active": "true", "closed": "false", "order": "volume", "ascending": "false"}
            resp = requests.get(GAMMA_API, params=params, timeout=20)
            return resp.json() if resp.status_code == 200 else []
        except Exception as e:
            logger.error(f"Error API: {e}")
            return []

    def select_fresh_tip(self, markets):
        """Busca una opción que no sea la anterior y que tenga sentido."""
        global last_posted_slug
        candidates = []
        
        for m in markets:
            slug = m.get("slug")
            # Ignoramos si es el mismo de la última vez o si es BitBoy por tercera vez
            if slug == last_posted_slug:
                continue
            
            # Filtro de palabras clave para variar el contenido
            if "bitboy" in slug.lower() and last_posted_slug and "bitboy" in last_posted_slug.lower():
                continue

            tokens = m.get("tokens", [])
            if not tokens: continue
            
            for t in tokens:
                try:
                    price = float(t.get("price", 0) or 0)
                    # Buscamos apuestas con valor real (ROI > 10%)
                    if 0.10 < price < 0.90:
                        vol = float(m.get("volume", 0) or 0)
                        # Score: Volumen + Diversidad
                        score = vol * (1.2 if "bitcoin" in slug.lower() else 1.0)
                        
                        candidates.append({
                            "q": m["question"], "out": t.get("outcome", "YES"),
                            "prob": price * 100, "roi": ((1/price)-1)*100,
                            "vol": vol, "slug": slug, "score": score
                        })
                except: continue

        if candidates:
            # Ordenamos por score y tomamos el mejor de los NUEVOS
            return sorted(candidates, key=lambda x: x["score"], reverse=True)[0]
        
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
        msg += "💎 <i>Live Intelligence by PolymarketBot</i>"
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
            logger.info("🔎 Scanning for a FRESH tip...")
            raw_markets = engine.fetch_markets()
            tip = engine.select_fresh_tip(raw_markets)
            
            if tip:
                logger.info(f"✅ Sending New Tip: {tip['q']}")
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": engine.format_post(tip),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
                requests.post(url, json=payload, timeout=15)
                
                last_posted_slug = tip['slug']
                logger.info(f"📱 Posted! Next check in {POST_INTERVAL_HOURS} hours.")
                time.sleep(POST_INTERVAL_HOURS * 3600)
            else:
                logger.warning("No NEW market found. Retrying in 5 minutes...")
                time.sleep(300)
                
        except Exception as e:
            logger.error(f"Error: {e}")
            time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=bot_main_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))