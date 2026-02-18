import os
import logging
import requests
import asyncio
from datetime import datetime
from typing import List, Dict, Any
from telegram import Bot
from telegram.constants import ParseMode
from dotenv import load_dotenv
from risk_manager import RiskManager

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
USER_BANKROLL = float(os.getenv("USER_BANKROLL", "1000"))

CONFIG = {
    "min_probability": 80,
    "max_probability": 95,
    "min_volume": 10000,
    "min_liquidity": 5000,
    "post_interval_hours": 6,
    "markets_per_post": 3,
}

GAMMA_API = "https://gamma-api.polymarket.com"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PolyBot:
    def __init__(self):
        if not TELEGRAM_BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN not found!")
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.risk = RiskManager(total_bankroll=USER_BANKROLL)

    def fetch_markets(self) -> List[Dict[str, Any]]:
        try:
            r = requests.get(f"{GAMMA_API}/markets", params={"limit": 100, "active": True}, timeout=15)
            return r.json()
        except Exception as e:
            logger.error(f"API Error: {e}")
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
            
            bt = max(tokens, key=lambda t: float(t.get('price', 0)))
            price = float(bt.get('price', 0))
            prob = price * 100
            
            if CONFIG['min_probability'] <= prob <= CONFIG['max_probability']:
                roi = ((1 / price) - 1) * 100
                filtered.append({
                    'question': m.get('question', 'N/A'),
                    'outcome': bt.get('outcome', 'YES'),
                    'prob': prob,
                    'price': price,
                    'roi': roi,
                    'vol': vol,
                    'liq': liq,
                    'slug': m.get('slug', '')
                })
        return sorted(filtered, key=lambda x: x['prob'], reverse=True)

    def format_html(self, markets: List[Dict[str, Any]]) -> str:
        if not markets:
            return "❌ No bets matching the criteria were found."

        now = datetime.now().strftime('%m/%d/%Y %H:%M')
        msg = "🚨 <b>NEW POLYMARKET SIGNALS</b> 🚨\n\n"
        msg += f"📊 {len(markets)} High-Probability Opportunities\n"
        msg += f"⏰ {now}\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n\n"

        for i, m in enumerate(markets, 1):
            msg += f"<b>#{i} {m['question']}</b>\n\n"
            msg += f"🎯 <b>Bet:</b> {m['outcome']}\n"
            msg += f"📈 <b>Probability:</b> {m['prob']:.1f}%\n"
            msg += f"💵 <b>Price:</b> ${m['price']:.3f}\n"
            msg += f"💰 <b>Potential ROI:</b> +{m['roi']:.1f}%\n"
            msg += f"📊 <b>Volume:</b> ${m['vol']:,.0f}\n"
            msg += f"💧 <b>Liquidity:</b> ${m['liq']:,.0f}\n\n"

            msg += "💸 <b>Estimated Gains:</b>\n"
            amounts = [1000, 5000, 10000]
            for amt in amounts:
                profit = (amt / m['price']) - amt
                msg += f" • ${amt:,} → +${profit:,.0f}\n"

            msg += f"\n🔗 <a href='https://polymarket.com/event/{m['slug']}'>View on Polymarket</a>\n"
            msg += "━━━━━━━━━━━━━━━━━━━━\n\n"

        msg += f"⚙️ <b>Filters:</b> {CONFIG['min_probability']}-{CONFIG['max_probability']}% prob"
        msg += f" | Min ${CONFIG['min_volume']:,} vol\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += "💎 <b>Don't miss these opportunities!</b>"
        return msg

    async def run(self):
        logger.info("Bot logic loop started...")
        while True:
            try:
                raw = self.fetch_markets()
                best = self.filter_best_bets(raw)[:CONFIG['markets_per_post']]
                
                if best:
                    message = self.format_html(best)
                    await self.bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=message,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )
                    logger.info("Signal sent to Telegram.")
                else:
                    logger.info("No markets matched filters.")
                
                await asyncio.sleep(CONFIG['post_interval_hours'] * 3600)
            except Exception as e:
                logger.error(f"Loop Error: {e}")
                await asyncio.sleep(300)

    async def run_once(self):
        raw = self.fetch_markets()
        best = self.filter_best_bets(raw)[:CONFIG['markets_per_post']]
        message = self.format_html(best)
        await self.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode=ParseMode.HTML)