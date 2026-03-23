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

GAMMA_EVENTS_API = "https://gamma-api.polymarket.com/events"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("polybot")

# Memoria temporal
last_posted_slug = ""

# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE: AGGRESSIVE REPORTER
# ═══════════════════════════════════════════════════════════════════════════════

class DailySignalEngine:
    def fetch_events(self):
        try:
            # Traemos los 50 eventos con más volumen histórico
            params = {"limit": 50, "active": "true", "closed": "false", "order": "volume", "ascending": "false"}
            resp = requests.get(GAMMA_EVENTS_API, params=params, timeout=20)
            return resp.json() if resp.status_code == 200 else []
        except Exception as e:
            logger.error(f"Error API: {e}")
            return []

    def get_any_good_tip(self, events):
        global last_posted_slug
        if not events: return None

        candidates = []
        for e in events:
            slug = e.get("slug")
            # Solo saltamos si es exactamente el último que publicamos en esta sesión
            if slug == last_posted_slug: continue

            markets = e.get("markets", [])
            if not markets: continue

            for m in markets:
                try:
                    prices = m.get("outcomePrices")
                    if not prices: continue
                    
                    price = float(prices[0])
                    vol = float(e.get("volume", 0) or 0)
                    
                    # Filtro MUY relajado: que tenga un precio y volumen
                    if 0.02 < price < 0.98:
                        roi = ((1 / price) - 1) * 100
                        score = vol + (roi * 5)
                        
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
            # Retornamos el de mayor puntuación
            return sorted(candidates, key=lambda x: x["score"], reverse=True)[0]
        
        # FALLBACK EXTREMO: Si nada pasa el filtro, enviamos el #1 en volumen
        logger.info("⚠️ Sin candidatos ideales. Enviando Top Volume por defecto.")
        top = events[0]
        return {
            "q": top.get("title"), "out": "YES/OUTCOME", "prob": 50.0,
            "roi": 100.0, "vol": float(top.get("volume", 0)),
            "slug": top.get("slug"), "score": 0
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
        msg += "💎 <b>Shared via @polymsignals</b>"
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
    
    # Reducimos la espera de estabilidad a solo 10 segundos
    logger.info("🚀 Bot iniciado. Escaneando de inmediato...")
    time.sleep(10)

    while True:
        try:
            logger.info("🔎 Buscando señal activa...")
            raw_events = engine.fetch_events()
            tip = engine.get_any_good_tip(raw_events)
            
            if tip:
                logger.info(f"✅ Publicando: {tip['q']}")
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": engine.format_post(tip),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
                requests.post(url, json=payload, timeout=15)
                
                last_posted_slug = tip['slug']
                
                # Esperamos el tiempo definido (ej. 24 horas)
                logger.info(f"📱 Post enviado. Esperando {POST_INTERVAL_HOURS} horas...")
                time.sleep(POST_INTERVAL_HOURS * 3600)
            else:
                logger.warning("No se encontró nada. Reintentando en 5 minutos.")
                time.sleep(300)
                
        except Exception as e:
            logger.error(f"Error en el bucle: {e}")
            time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=bot_main_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))