import os
import time
import logging
import threading
import random
import json
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

GAMMA_API = "https://gamma-api.polymarket.com/events"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("polybot")

posted_history = []

# ═══════════════════════════════════════════════════════════════════════════════
# REFINED SELECTION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class RandomSignalEngine:
    def fetch_pool(self):
        try:
            params = {"limit": 60, "active": "true", "closed": "false", "order": "volume", "ascending": "false"}
            resp = requests.get(GAMMA_API, params=params, timeout=15)
            return resp.json() if resp.status_code == 200 else []
        except: return []

    def get_random_unique_tip(self, events):
        global posted_history
        if not events: return None
        
        # Filtramos los que ya publicamos
        available = [e for e in events if e.get("slug") not in posted_history]
        if not available:
            posted_history = []
            available = events

        # Elegimos uno al azar
        selected_event = random.choice(available)
        markets = selected_event.get("markets", [])
        if not markets: return None

        m = markets[0]
        try:
            # CORRECCIÓN DE PRECIOS Y POSICIÓN
            # outcomePrices suele ser un string JSON en la API Gamma
            prices_raw = m.get("outcomePrices", "[0.5, 0.5]")
            if isinstance(prices_raw, str):
                prices = json.loads(prices_raw)
            else:
                prices = prices_raw
                
            # Tomamos el primer outcome (YES o el nombre del favorito)
            outcomes = m.get("outcomes", ["Yes", "No"])
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes)

            price = float(prices[0])
            position_name = str(outcomes[0]) # Limpiamos el nombre para que no salga "["

            return {
                "q": selected_event.get("title", m.get("question")),
                "out": position_name,
                "prob": price * 100,
                "roi": ((1/price)-1)*100 if price > 0 else 100,
                "vol": float(selected_event.get("volume", 0) or 0),
                "slug": selected_event.get("slug")
            }
        except Exception as e:
            logger.error(f"Data parse error: {e}")
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
def health(): return "Bot is healthy", 200

def bot_main_loop():
    global posted_history
    engine = RandomSignalEngine()
    time.sleep(15) # Espera inicial para Render

    while True:
        try:
            logger.info("🎲 Scanning for market...")
            pool = engine.fetch_pool()
            tip = engine.get_random_unique_tip(pool)
            
            if tip:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": engine.format_post(tip),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
                resp = requests.post(url, json=payload, timeout=15)
                
                if resp.status_code == 200:
                    posted_history.append(tip['slug'])
                    if len(posted_history) > 20: posted_history.pop(0)
                    logger.info(f"📱 Sent: {tip['q']}. Waiting {POST_INTERVAL_HOURS}h.")
                    time.sleep(POST_INTERVAL_HOURS * 3600)
                else:
                    time.sleep(300)
            else:
                time.sleep(300)
                
        except Exception as e:
            logger.error(f"Loop error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=bot_main_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)