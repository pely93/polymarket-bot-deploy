import os
import logging
import requests
import asyncio
from datetime import datetime
from typing import List, Dict, Any
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

# --- CONFIGURATION (Safe) ---
# We use os.getenv to keep these secret
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CONFIG = {
    "min_probability": 80,
    "max_probability": 95,
    "min_volume": 10000,
    "min_liquidity": 5000,
    "post_interval_hours": 6,
    "markets_per_post": 3,
}

GAMMA_API = "https://gamma-api.polymarket.com"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

class PolyBot:
    def __init__(self):
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            raise ValueError("CRITICAL: Environment variables not set!")
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)

    def fetch_markets(self) -> List[Dict[str, Any]]:
        try:
            url = f"{GAMMA_API}/markets"
            params = {"limit": 100, "closed": False, "active": True}
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Fetch Error: {e}")
            return []

    def filter_best_bets(self, markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        filtered = []
        for m in markets:
            vol = float(m.get('volume', 0))
            liq = float(m.get('liquidity', 0))
            if vol < CONFIG['min_volume'] or liq < CONFIG['min_liquidity']:
                continue

            tokens = m.get('tokens', [])
            if not tokens: continue
            
            best_token = max(tokens, key=lambda t: float(t.get('price', 0)))
            price = float(best_token.get('price', 0))
            prob = price * 100

            if CONFIG['min_probability'] <= prob <= CONFIG['max_probability']:
                roi = ((1 / price) - 1) * 100
                filtered.append({
                    'question': m.get('question', 'N/A'),
                    'outcome': best_token.get('outcome', 'YES'),
                    'prob': prob,
                    'roi': roi,
                    'slug': m.get('slug', '')
                })
        return sorted(filtered, key=lambda x: x['prob'], reverse=True)

    def format_html(self, markets: List[Dict[str, Any]]) -> str:
        if not markets: return "<b>No matches found currently.</b>"
        msg = "🚨 <b>POLYMARKET SIGNAL</b> 🚨\n\n"
        for i, m in enumerate(markets, 1):
            msg += f"<b>{i}. {m['question']}</b>\n"
            msg += f"🎯 <b>Result:</b> <code>{m['outcome']}</code>\n"
            msg += f"📈 <b>Prob:</b> <code>{m['prob']:.1f}%</code>\n"
            msg += f"💰 <b>ROI:</b> <code>+{m['roi']:.1f}%</code>\n"
            msg += f"🔗 <a href='https://polymarket.com/event/{m['slug']}'>Trade Here</a>\n\n"
        return msg

    async def run(self):
        logger.info("Bot logic loop started...")
        while True:
            try:
                raw = self.fetch_markets()
                best = self.filter_best_bets(raw)[:CONFIG['markets_per_post']]
                message = self.format_html(best)
                
                await self.bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=message,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )
                logger.info("Signal sent to Telegram.")
                await asyncio.sleep(CONFIG['post_interval_hours'] * 3600)
            except Exception as e:
                logger.error(f"Loop Error: {e}")
                await asyncio.sleep(300)