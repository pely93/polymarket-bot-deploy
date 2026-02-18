import os
import logging
import requests
import asyncio
from datetime import datetime
from typing import List, Dict, Any
from telegram import Bot
from telegram.constants import ParseMode
from risk_manager import RiskManager

# --- CONFIG ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
USER_BANKROLL = float(os.getenv("USER_BANKROLL", 1000.0)) # Default $1000

CONFIG = {
    "min_probability": 80,
    "max_probability": 95,
    "min_volume": 10000,
    "post_interval_hours": 6,
    "markets_per_post": 3,
}

GAMMA_API = "https://gamma-api.polymarket.com"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PolyBot:
    def __init__(self):
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.risk = RiskManager(total_bankroll=USER_BANKROLL)

    def fetch_markets(self):
        try:
            r = requests.get(f"{GAMMA_API}/markets", params={"limit": 100, "active": True})
            return r.json()
        except: return []

    def format_signal(self, m: dict) -> str:
        # We assume a 2% edge for demonstration
        # In real usage, you'd provide your own 'p'
        my_estimated_prob = m['prob'] + 2.0 
        calc = self.risk.calculate_bet(m['price'], my_estimated_prob)

        msg = (
            f"<b>{m['question']}</b>\n"
            f"🎯 <b>Bet:</b> <code>{m['outcome']}</code>\n"
            f"📈 <b>Market Prob:</b> {m['prob']:.1f}%\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🛡️ <b>RISK MANAGEMENT (1/4 Kelly)</b>\n"
            f"💰 <b>Bankroll:</b> ${USER_BANKROLL:,.0f}\n"
            f"💸 <b>Suggested Bet:</b> <code>${calc['suggested_usd']}</code>\n"
            f"📊 <b>Size:</b> {calc['percentage']}% of funds\n"
            f"🔗 <a href='https://polymarket.com/event/{m['slug']}'>Open Trade</a>\n\n"
        )
        return msg

    async def run(self):
        while True:
            raw = self.fetch_markets()
            best_bets = []
            for market in raw:
                # Basic filter logic from previous step
                vol = float(market.get('volume', 0))
                if vol < CONFIG['min_volume']: continue
                tokens = market.get('tokens', [])
                if not tokens: continue
                bt = max(tokens, key=lambda t: float(t.get('price', 0)))
                price = float(bt.get('price', 0))
                prob = price * 100
                if CONFIG['min_probability'] <= prob <= CONFIG['max_probability']:
                    best_bets.append({
                        'question': market.get('question'),
                        'outcome': bt.get('outcome'),
                        'price': price,
                        'prob': prob,
                        'slug': market.get('slug')
                    })
            
            if best_bets:
                top = sorted(best_bets, key=lambda x: x['prob'], reverse=True)[:3]
                full_msg = "🚨 <b>NEW SIGNALS DETECTED</b>\n\n"
                for b in top:
                    full_msg += self.format_signal(b)
                
                await self.bot.send_message(TELEGRAM_CHAT_ID, full_msg, parse_mode=ParseMode.HTML)
            
            await asyncio.sleep(CONFIG['post_interval_hours'] * 3600)

if __name__ == "__main__":
    asyncio.run(PolyBot().run())