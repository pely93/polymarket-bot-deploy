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

# Usamos el endpoint simplificado para evitar bloqueos de filtros
GAMMA_API = "https://gamma-api.polymarket.com/markets"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("polybot")

# ═══════════════════════════════════════════════════════════════════════════════
# ANALYTICS ENGINE (REDISEÑO TOTAL)
# ═══════════════════════════════════════════════════════════════════════════════

class ProAnalyticsEngine:
    def fetch_markets(self):
        """Intenta obtener mercados de varias formas si una falla."""
        urls = [
            f"{GAMMA_API}?limit=50&active=true&closed=false",
            f"{GAMMA_API}?limit=20&active=true",
            "https://gamma-api.polymarket.com/markets" # Último recurso
        ]
        
        for url in urls:
            try:
                logger.info(f"Connecting to: {url}")
                resp = requests.get(url, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    if data and len(data) > 0:
                        return data
            except Exception as e:
                logger.error(f"Error on {url}: {e}")
                continue
        return []

    def get_best_daily_signal(self, markets):
        if not markets:
            return None

        candidates = []
        for m in markets:
            # Ignorar si no es una pregunta real
            if not m.get("question") or "test" in m.get("question", "").lower():
                continue
                
            tokens = m.get("tokens", [])
            if not tokens: continue
            
            # Buscamos el mejor resultado dentro de este mercado
            for t in tokens:
                try:
                    price = float(t.get("price", 0) or 0)
                    if 0.01 < price < 0.99: # Rango casi total
                        vol = float(m.get("volume", 0) or 0)
                        # Score simple: Volumen es el rey
                        score = vol + (price * 100)
                        
                        candidates.append({
                            "q": m["question"],
                            "out": t.get("outcome", "YES"),
                            "prob": price * 100,
                            "roi": ((1/price)-1)*100,
                            "vol": vol,
                            "liq": float(m.get("liquidity", 0) or 0),
                            "slug": m.get("slug", ""),
                            "score": score
                        })
                except: continue

        # Si tenemos candidatos, devolvemos el mejor. Si no, forzamos el primero de la lista.
        if candidates:
            return max(candidates, key=lambda x: x["score"])
        
        # Fallback extremo: Tomar el primer mercado que tenga una pregunta
        m = markets[0]
        return {
            "q": m.get("question", "Market Tip"),
            "out": "YES",
            "prob": 50.0,
            "roi": 100.0,
            "vol": float(m.get("volume", 0) or 0),
            "liq": float(m.get("liquidity", 0) or 0),
            "slug": m.get("slug", ""),
            "score": 0
        }

    def format_message(self, tip):
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
        msg += "💎 <i>Shared via Polymarket Tipster</i>"
        return msg

# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
@app.route("/")
def health(): return "OK", 200

def bot_main_loop():
    engine = ProAnalyticsEngine()
    logger.info("🚀 Professional Daily Signal Engine Started")
    
    while True:
        try:
            logger.info("🔎 Scanning markets...")
            raw = engine.fetch_markets()
            
            if not raw:
                logger.warning("❌ No data from API. Retrying in 2 minutes...")
                time.sleep(120)
                continue

            best_tip = engine.get_best_daily_signal(raw)
            
            if best_tip:
                logger.info(f"✅ Selected: {best_tip['q']}")
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": engine.format_message(best_tip),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
                resp = requests.post(url, json=payload, timeout=15)
                
                if resp.status_code == 200:
                    logger.info("📱 Success! Waiting for next cycle.")
                    time.sleep(POST_INTERVAL_HOURS * 3600)
                else:
                    logger.error(f"❌ Telegram Fail: {resp.text}")
                    time.sleep(300)
            
        except Exception as e:
            logger.error(f"💥 Critical Error: {e}")
            time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=bot_main_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))