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
# Forzamos a que revise cada hora, pero solo postee una vez por fecha
SCAN_INTERVAL_SECONDS = 3600 

GAMMA_EVENTS_API = "https://gamma-api.polymarket.com/events"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("polybot")

# Variables de estado (Se mantienen mientras el proceso esté vivo)
last_post_date = "" # Formato YYYY-MM-DD
last_posted_slug = ""

# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE: DAILY GUARANTEE
# ═══════════════════════════════════════════════════════════════════════════════

class DailySignalEngine:
    def fetch_events(self):
        try:
            params = {"limit": 50, "active": "true", "closed": "false", "order": "volume", "ascending": "false"}
            resp = requests.get(GAMMA_EVENTS_API, params=params, timeout=20)
            return resp.json() if resp.status_code == 200 else []
        except Exception as e:
            logger.error(f"API Error: {e}")
            return []

    def get_daily_tip(self, events):
        global last_posted_slug
        if not events: return None

        candidates = []
        for e in events:
            slug = e.get("slug")
            # Evitamos repetir el mismo mercado exacto
            if slug == last_posted_slug:
                continue

            markets = e.get("markets", [])
            if not markets: continue

            for m in markets:
                try:
                    prices = m.get("outcomePrices")
                    if not prices: continue
                    
                    price = float(prices[0])
                    vol = float(e.get("volume", 0) or 0)
                    
                    # Filtro de calidad para el post diario
                    if 0.10 < price < 0.90:
                        roi = ((1 / price) - 1) * 100
                        score = (vol * 0.8) + (roi * 10) # Priorizamos volumen
                        
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

        # Devolvemos el mejor que no hayamos posteado
        if candidates:
            return max(candidates, key=lambda x: x["score"])
        
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
        msg += "💎 <b>Shared via @polymsignals</b>"
        return msg

# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
@app.route("/")
def health(): return "OK", 200

def bot_main_loop():
    global last_post_date, last_posted_slug
    engine = DailySignalEngine()
    
    logger.info("🚀 Bot started. Waiting 30s for stability...")
    time.sleep(30)

    while True:
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            
            # Si ya posteamos hoy, esperamos a la siguiente revisión
            if last_post_date == today:
                logger.info(f"📅 Ya se publicó el post de hoy ({today}). Durmiendo 1 hora...")
                time.sleep(3600)
                continue

            logger.info(f"🔎 Buscando el post para hoy: {today}")
            raw_events = engine.fetch_events()
            tip = engine.get_daily_tip(raw_events)
            
            if tip:
                logger.info(f"✅ Seleccionado para hoy: {tip['q']}")
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": engine.format_post(tip),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
                resp = requests.post(url, json=payload, timeout=15)
                
                if resp.status_code == 200:
                    last_post_date = today
                    last_posted_slug = tip['slug']
                    logger.info("📱 Post enviado con éxito.")
                else:
                    logger.error(f"❌ Error de Telegram: {resp.text}")
            
            # Revisar cada hora
            time.sleep(SCAN_INTERVAL_SECONDS)
                
        except Exception as e:
            logger.error(f"Error: {e}")
            time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=bot_main_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))