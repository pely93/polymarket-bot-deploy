"""
==============================================================================
  POLYMARKET SMART MONEY TIPSTER — COMBINED PRODUCTION BOT
  ─────────────────────────────────────────────────────────
  Merges your existing market-scanning bot with the 5-layer smart money
  filter into a single deployable service for Render.com.

  TWO SIGNAL ENGINES:
    1. Market Scanner  — Finds high-probability markets (your original logic)
    2. Smart Money Tracker — Monitors top leaderboard wallets for new trades

  FIXES FROM YOUR ORIGINAL CODE:
    - Render health check now works (Flask responds before bot starts)
    - python-dotenv added to requirements
    - Proper async/thread separation
    - Graceful error recovery
    - Logging to stdout (visible in Render logs)
==============================================================================
"""

import os
import sys
import json
import time
import logging
import threading
import requests
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from flask import Flask
from dotenv import load_dotenv

# ─── Load .env for local dev (Render uses Environment Variables directly) ───
load_dotenv()

# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING — Render captures stdout, so we write there
# ═══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("polybot")

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION (all from environment variables with sensible defaults)
# ═══════════════════════════════════════════════════════════════════════════════

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
USER_BANKROLL = float(os.getenv("USER_BANKROLL", "1000"))

# --- API Endpoints ---
BASE_DATA_API = "https://data-api.polymarket.com"
BASE_GAMMA_API = "https://gamma-api.polymarket.com"

# --- Market Scanner Config (Engine 1 — your original logic, improved) ---
MARKET_SCANNER = {
    "enabled": True,
    "min_probability": 65,           # Lowered from 80 — 80-95% leaves very few markets
    "max_probability": 92,           # Cap at 92% — above this there's no edge
    "min_volume": 10_000,
    "min_liquidity": 5_000,
    "markets_per_post": 5,
    "scan_interval_hours": 6,
}

# --- Smart Money Tracker Config (Engine 2 — the 5-layer filter) ---
SMART_MONEY = {
    "enabled": True,
    "leaderboard_refresh_hours": 6,
    "trade_poll_seconds": 60,
    "activity_lookback_seconds": 900,  # 15 minutes — wide enough to never miss trades

    # Layer 1: Leaderboard pre-filter
    "min_pnl_all_time": 5_000,
    "min_volume_all_time": 50_000,

    # Layer 2: Multi-timeframe consistency
    "min_profitable_windows": 2,     # Must be profitable in 2+ of {DAY, WEEK, MONTH, ALL}

    # Layer 3: Win rate & ROI from closed positions
    "min_closed_positions": 8,
    "min_win_rate": 0.54,
    "min_roi_percent": 8,

    # Layer 4: Per-trade quality
    "min_trade_size_usd": 200,
    "min_market_liquidity": 8_000,
    "max_probability": 0.92,
    "min_probability": 0.05,
    "longshot_min_trade_usd": 1_000,

    # Layer 5: Convergence
    "convergence_window_minutes": 60,
    "convergence_min_wallets": 2,
}


# ═══════════════════════════════════════════════════════════════════════════════
# RISK MANAGER (your Kelly Criterion logic, kept intact)
# ═══════════════════════════════════════════════════════════════════════════════

class RiskManager:
    def __init__(self, total_bankroll: float, kelly_fraction: float = 0.25):
        self.bankroll = total_bankroll
        self.fraction = kelly_fraction

    def calculate_bet(self, market_price: float, user_prob: float) -> dict:
        p = user_prob / 100.0
        q = 1.0 - p

        if market_price <= 0 or market_price >= 1:
            return {"suggested_usd": 0, "percentage": 0, "edge": 0}

        b = (1.0 - market_price) / market_price
        raw_f = (b * p - q) / b
        optimal_f = max(0, raw_f * self.fraction)
        safe_f = min(optimal_f, 0.10)
        suggested_usd = self.bankroll * safe_f

        return {
            "suggested_usd": round(suggested_usd, 2),
            "percentage": round(safe_f * 100, 2),
            "edge": round((p - market_price) * 100, 2),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# SMART MONEY DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SmartWallet:
    address: str
    username: str
    pnl_all: float = 0.0
    vol_all: float = 0.0
    pnl_month: float = 0.0
    pnl_week: float = 0.0
    pnl_day: float = 0.0
    profitable_windows: int = 0
    win_rate: float = 0.0
    roi_percent: float = 0.0
    closed_positions_count: int = 0
    tier: str = "B"
    last_seen_trade_ts: int = 0


@dataclass
class Signal:
    wallet_address: str
    wallet_username: str
    wallet_tier: str
    market_question: str
    market_slug: str
    outcome: str
    side: str
    size_tokens: float
    price: float
    estimated_usd: float
    market_probability: float
    market_liquidity: float
    timestamp: int
    convergence_count: int = 1


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED API HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def api_get(url: str, params: dict = None, retries: int = 3) -> Optional[Any]:
    """Robust API caller with retry and rate-limit handling."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=20)
            if resp.status_code == 429:
                wait = 2 ** attempt * 5
                logger.warning(f"Rate limited on {url}, waiting {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt + 1)
            else:
                logger.error(f"API failed after {retries} attempts: {url} — {e}")
                return None
    return None


def send_telegram(message: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the Telegram channel. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram credentials missing!")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()
        if not data.get("ok"):
            logger.error(f"Telegram API error: {data}")
            return False
        return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def get_market_details(condition_id: str) -> dict:
    """Fetch human-readable market info from the Gamma API."""
    data = api_get(f"{BASE_GAMMA_API}/markets", {"condition_id": condition_id})
    if data and len(data) > 0:
        m = data[0]
        return {
            "question": m.get("question", "Unknown Market"),
            "slug": m.get("slug", ""),
            "outcomes": m.get("outcomes", '["Yes","No"]'),
            "outcome_prices": m.get("outcomePrices", '["0.5","0.5"]'),
            "volume": float(m.get("volume", 0) or 0),
            "liquidity": float(m.get("liquidity", 0) or 0),
            "active": m.get("active", False),
            "closed": m.get("closed", False),
        }
    return {
        "question": "Unknown", "slug": "", "outcomes": "[]",
        "outcome_prices": "[]", "volume": 0, "liquidity": 0,
        "active": False, "closed": True,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE 1: MARKET SCANNER (your original logic, improved)
# ═══════════════════════════════════════════════════════════════════════════════

class MarketScanner:
    """Scans Polymarket for high-probability bets and broadcasts them."""

    def __init__(self):
        self.risk = RiskManager(total_bankroll=USER_BANKROLL)
        self.cfg = MARKET_SCANNER

    def fetch_markets(self) -> List[Dict[str, Any]]:
        data = api_get(f"{BASE_GAMMA_API}/markets", {
            "limit": 100,
            "active": "true",
            "closed": "false",
        })
        return data if data else []

    def filter_best_bets(self, markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        filtered = []
        for m in markets:
            vol = float(m.get("volume", 0) or 0)
            liq = float(m.get("liquidity", 0) or 0)
            if vol < self.cfg["min_volume"] or liq < self.cfg["min_liquidity"]:
                continue

            tokens = m.get("tokens", [])
            if not tokens:
                continue

            # Find the highest-priced outcome (most probable)
            bt = max(tokens, key=lambda t: float(t.get("price", 0) or 0))
            price = float(bt.get("price", 0) or 0)
            if price <= 0 or price >= 1:
                continue

            prob = price * 100
            if not (self.cfg["min_probability"] <= prob <= self.cfg["max_probability"]):
                continue

            roi = ((1 / price) - 1) * 100

            # Kelly sizing
            kelly = self.risk.calculate_bet(price, prob + 2)  # Assume 2% edge estimate

            filtered.append({
                "question": m.get("question", "N/A"),
                "outcome": bt.get("outcome", "YES"),
                "prob": prob,
                "price": price,
                "roi": roi,
                "vol": vol,
                "liq": liq,
                "slug": m.get("slug", ""),
                "kelly": kelly,
            })

        return sorted(filtered, key=lambda x: (-x["prob"], -x["vol"]))

    def format_alert(self, markets: List[Dict[str, Any]]) -> str:
        if not markets:
            return ""

        now = datetime.now(timezone.utc).strftime("%m/%d/%Y %H:%M UTC")
        msg = "📊 <b>MARKET SCANNER — HIGH PROBABILITY PLAYS</b>\n"
        msg += f"⏰ {now}\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n\n"

        for i, m in enumerate(markets, 1):
            msg += f"<b>#{i} {m['question']}</b>\n\n"
            msg += f"🎯 Bet: <b>{m['outcome']}</b>\n"
            msg += f"📈 Probability: <b>{m['prob']:.1f}%</b>\n"
            msg += f"💵 Price: ${m['price']:.3f}\n"
            msg += f"💰 Potential ROI: +{m['roi']:.1f}%\n"
            msg += f"📊 Volume: ${m['vol']:,.0f} | Liquidity: ${m['liq']:,.0f}\n"

            if m["kelly"]["suggested_usd"] > 0:
                msg += f"🧮 Kelly suggests: ${m['kelly']['suggested_usd']:,.0f} "
                msg += f"({m['kelly']['percentage']:.1f}% of bankroll)\n"

            msg += f"\n🔗 <a href='https://polymarket.com/event/{m['slug']}'>View Market</a>\n"
            msg += "━━━━━━━━━━━━━━━━━━━━\n\n"

        msg += f"⚙️ Filters: {self.cfg['min_probability']}-{self.cfg['max_probability']}% prob"
        msg += f" | Vol ≥${self.cfg['min_volume']:,} | Liq ≥${self.cfg['min_liquidity']:,}"
        return msg

    def run_cycle(self):
        """Run one scan cycle. Returns True if a message was sent."""
        logger.info("[Scanner] Fetching markets...")
        raw = self.fetch_markets()
        logger.info(f"[Scanner] Got {len(raw)} markets from Gamma API")

        best = self.filter_best_bets(raw)[: self.cfg["markets_per_post"]]
        logger.info(f"[Scanner] {len(best)} markets passed filters")

        if best:
            message = self.format_alert(best)
            ok = send_telegram(message)
            if ok:
                logger.info(f"[Scanner] Sent {len(best)} market signals to Telegram")
            return ok
        else:
            logger.info("[Scanner] No markets matched filters this cycle")
            return False


# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE 2: SMART MONEY TRACKER (5-layer filter)
# ═══════════════════════════════════════════════════════════════════════════════

class SmartMoneyTracker:
    """Monitors top leaderboard wallets and alerts on their new trades."""

    def __init__(self):
        self.cfg = SMART_MONEY
        self.tracked_wallets: Dict[str, SmartWallet] = {}
        self.recent_signals: List[Signal] = []
        self.last_leaderboard_scan = 0

    # ── Layer 1 + 2: Leaderboard scanning ──

    def scan_leaderboard(self) -> Dict[str, SmartWallet]:
        logger.info("[SmartMoney] Layer 1+2: Scanning leaderboard...")
        candidates = {}
        categories = ["OVERALL", "POLITICS", "SPORTS", "CRYPTO", "ECONOMICS"]
        time_periods = ["DAY", "WEEK", "MONTH", "ALL"]

        for category in categories:
            for period in time_periods:
                entries = api_get(f"{BASE_DATA_API}/v1/leaderboard", {
                    "category": category,
                    "timePeriod": period,
                    "orderBy": "PNL",
                    "limit": 50,
                })
                time.sleep(0.4)

                if not entries:
                    continue

                for entry in entries:
                    wallet = entry.get("proxyWallet", "")
                    if not wallet:
                        continue

                    pnl = float(entry.get("pnl", 0) or 0)
                    vol = float(entry.get("vol", 0) or 0)
                    username = entry.get("userName", "") or wallet[:10]

                    if wallet not in candidates:
                        candidates[wallet] = SmartWallet(address=wallet, username=username)

                    sw = candidates[wallet]
                    if period == "ALL":
                        sw.pnl_all = max(sw.pnl_all, pnl)
                        sw.vol_all = max(sw.vol_all, vol)
                    elif period == "MONTH":
                        sw.pnl_month = max(sw.pnl_month, pnl)
                    elif period == "WEEK":
                        sw.pnl_week = max(sw.pnl_week, pnl)
                    elif period == "DAY":
                        sw.pnl_day = max(sw.pnl_day, pnl)

        logger.info(f"[SmartMoney] Found {len(candidates)} unique wallets")

        # Layer 1: Hard filters
        layer1 = {
            a: s for a, s in candidates.items()
            if s.pnl_all >= self.cfg["min_pnl_all_time"]
            and s.vol_all >= self.cfg["min_volume_all_time"]
        }
        logger.info(f"[SmartMoney] Layer 1 pass: {len(layer1)}")

        # Layer 2: Multi-timeframe consistency
        for sw in layer1.values():
            sw.profitable_windows = sum([
                sw.pnl_day > 0, sw.pnl_week > 0,
                sw.pnl_month > 0, sw.pnl_all > 0,
            ])

        layer2 = {
            a: s for a, s in layer1.items()
            if s.profitable_windows >= self.cfg["min_profitable_windows"]
        }
        logger.info(f"[SmartMoney] Layer 2 pass: {len(layer2)}")
        return layer2

    # ── Layer 3: Win rate validation ──

    def validate_track_records(self, candidates: Dict[str, SmartWallet]) -> Dict[str, SmartWallet]:
        logger.info("[SmartMoney] Layer 3: Validating track records...")
        validated = {}

        for addr, sw in candidates.items():
            # CRITICAL: Sort by TIMESTAMP not REALIZEDPNL to get an unbiased
            # sample of wins AND losses. Sorting by PnL DESC only shows winners,
            # which inflates win rate to 100% for everyone.
            # Fetch two pages (100 positions total) for a better sample.
            all_closed = []
            for offset in [0, 50]:
                page = api_get(f"{BASE_DATA_API}/closed-positions", {
                    "user": addr, "limit": 50,
                    "sortBy": "TIMESTAMP", "sortDirection": "DESC",
                    "offset": offset,
                })
                time.sleep(0.3)
                if page:
                    all_closed.extend(page)
                else:
                    break  # No more pages or API error

            if not all_closed or len(all_closed) < self.cfg["min_closed_positions"]:
                continue

            wins, losses, total, total_pnl, total_initial = 0, 0, 0, 0.0, 0.0
            for pos in all_closed:
                # closed-positions returns: realizedPnl, totalBought, avgPrice
                realized_pnl = float(pos.get("realizedPnl", 0) or 0)
                total_bought = float(pos.get("totalBought", 0) or 0)
                avg_price = float(pos.get("avgPrice", 0) or 0)
                initial = total_bought * avg_price  # reconstruct initial value
                if abs(initial) < 1:
                    continue
                total += 1
                total_pnl += realized_pnl
                total_initial += abs(initial)
                if realized_pnl > 0:
                    wins += 1
                else:
                    losses += 1

            if total < self.cfg["min_closed_positions"]:
                continue

            wr = wins / total if total > 0 else 0
            roi = (total_pnl / total_initial * 100) if total_initial > 0 else 0

            sw.win_rate = round(wr, 3)
            sw.roi_percent = round(roi, 1)
            sw.closed_positions_count = total

            if wr >= self.cfg["min_win_rate"] and roi >= self.cfg["min_roi_percent"]:
                if wr >= 0.65 and roi >= 50:
                    sw.tier = "A"
                elif wr >= 0.58 and roi >= 25:
                    sw.tier = "B"
                else:
                    sw.tier = "C"
                validated[addr] = sw
                logger.info(f"  ✓ {sw.username}: WR={wr:.0%} ({wins}W/{losses}L) ROI={roi:.1f}% Tier={sw.tier}")

        logger.info(f"[SmartMoney] Layer 3 pass: {len(validated)} wallets")
        return validated

    # ── Layer 4: Monitor for new trades ──

    def check_new_trades(self) -> List[Signal]:
        now = int(datetime.now(timezone.utc).timestamp())
        lookback = now - self.cfg["activity_lookback_seconds"]
        signals = []
        seen_tx = set()  # Deduplicate by transaction hash
        total_trades_found = 0
        total_filtered_out = 0
        filter_reasons = {}

        for addr, sw in self.tracked_wallets.items():
            # Fetch both BUY and SELL — subscribers need to know exits too
            trades = api_get(f"{BASE_DATA_API}/activity", {
                "user": addr, "type": "TRADE",
                "start": lookback, "limit": 50,
                "sortBy": "TIMESTAMP", "sortDirection": "DESC",
            })
            time.sleep(0.3)

            if not trades:
                continue

            for trade in trades:
                ts = int(trade.get("timestamp", 0) or 0)
                if ts <= sw.last_seen_trade_ts:
                    continue

                total_trades_found += 1

                tx_hash = trade.get("transactionHash", "")
                if tx_hash in seen_tx:
                    continue
                seen_tx.add(tx_hash)

                side = trade.get("side", "BUY")
                size = float(trade.get("size", 0) or 0)
                price = float(trade.get("price", 0) or 0)
                # The API returns usdcSize as the dollar value of the trade
                usdc_size = float(trade.get("usdcSize", 0) or 0)
                # Fallback: size is in shares, price is $/share, so size*price = USD
                if usdc_size <= 0:
                    usdc_size = size * price

                if price <= 0:
                    filter_reasons["zero_price"] = filter_reasons.get("zero_price", 0) + 1
                    total_filtered_out += 1
                    continue

                condition_id = trade.get("conditionId", "")
                outcome = trade.get("outcome", "Unknown")
                title = trade.get("title", "")
                slug = trade.get("slug", "")
                event_slug = trade.get("eventSlug", slug)

                # 4A: Min trade size (USD)
                # Log first few filtered trades to understand the data
                if usdc_size < self.cfg["min_trade_size_usd"]:
                    if filter_reasons.get("too_small", 0) < 3:
                        logger.info(
                            f"  [debug] Filtered too_small: {sw.username} | "
                            f"usdcSize={trade.get('usdcSize')} size={size} price={price} "
                            f"calc_usd={usdc_size:.2f} | {title[:40]}"
                        )
                    filter_reasons["too_small"] = filter_reasons.get("too_small", 0) + 1
                    total_filtered_out += 1
                    continue

                # Fetch market details for liquidity and current probability
                # But do NOT use Gamma's active/closed flags to reject — they lag behind
                if condition_id:
                    market = get_market_details(condition_id)
                    time.sleep(0.15)
                else:
                    market = {
                        "question": title or "Unknown", "slug": slug,
                        "liquidity": 0, "active": True, "closed": False,
                        "outcome_prices": "[]",
                    }

                # Only skip markets that are TRULY closed/resolved
                # The Gamma API's "active" flag lags — a market someone just traded
                # on is clearly still active, so we trust the trade over the metadata
                market_closed = market.get("closed", False)
                if market_closed:
                    if filter_reasons.get("closed_inactive", 0) < 3:
                        logger.info(
                            f"  [debug] Filtered closed: {title[:40]} | "
                            f"closed={market.get('closed')} active={market.get('active')}"
                        )
                    filter_reasons["closed_inactive"] = filter_reasons.get("closed_inactive", 0) + 1
                    total_filtered_out += 1
                    continue

                # 4B: Liquidity check
                liq = float(market.get("liquidity", 0) or 0)
                if liq < self.cfg["min_market_liquidity"]:
                    filter_reasons["low_liquidity"] = filter_reasons.get("low_liquidity", 0) + 1
                    total_filtered_out += 1
                    continue

                # 4C: Get CURRENT probability (not the price they bought at)
                try:
                    prices = json.loads(market.get("outcome_prices", "[]"))
                    idx = int(trade.get("outcomeIndex", 0))
                    current_prob = float(prices[idx]) if prices else price
                except (json.JSONDecodeError, IndexError, ValueError):
                    current_prob = price

                # For BUY signals: skip if market is already too decided
                if side == "BUY":
                    if current_prob > self.cfg["max_probability"]:
                        filter_reasons["prob_too_high"] = filter_reasons.get("prob_too_high", 0) + 1
                        total_filtered_out += 1
                        continue
                    if current_prob < self.cfg["min_probability"] and usdc_size < self.cfg["longshot_min_trade_usd"]:
                        filter_reasons["longshot_small"] = filter_reasons.get("longshot_small", 0) + 1
                        total_filtered_out += 1
                        continue

                signals.append(Signal(
                    wallet_address=addr,
                    wallet_username=sw.username,
                    wallet_tier=sw.tier,
                    market_question=market.get("question", title),
                    market_slug=event_slug or market.get("slug", slug),
                    outcome=outcome,
                    side=side,
                    size_tokens=size,
                    price=price,
                    estimated_usd=usdc_size,
                    market_probability=current_prob,
                    market_liquidity=liq,
                    timestamp=ts,
                ))

            # Update last seen
            if trades:
                max_ts = max(int(t.get("timestamp", 0) or 0) for t in trades)
                sw.last_seen_trade_ts = max(sw.last_seen_trade_ts, max_ts)

        # Diagnostic logging — helps debug why signals aren't firing
        if total_trades_found > 0 or len(signals) > 0:
            logger.info(
                f"[SmartMoney] Poll result: {total_trades_found} new trades found, "
                f"{total_filtered_out} filtered out, {len(signals)} signals generated. "
                f"Filter reasons: {filter_reasons}"
            )

        return signals

    # ── Layer 5: Convergence ──

    def check_convergence(self, new_signals: List[Signal]) -> List[Signal]:
        now = int(datetime.now(timezone.utc).timestamp())
        window = self.cfg["convergence_window_minutes"] * 60
        self.recent_signals = [s for s in self.recent_signals if (now - s.timestamp) < window]
        self.recent_signals.extend(new_signals)

        for sig in new_signals:
            matching = [
                s for s in self.recent_signals
                if s.market_slug == sig.market_slug
                and s.outcome == sig.outcome
                and s.wallet_address != sig.wallet_address
                and abs(s.timestamp - sig.timestamp) < window
            ]
            sig.convergence_count = 1 + len(set(s.wallet_address for s in matching))

        return new_signals

    # ── Alert formatting ──

    def format_signal(self, sig: Signal) -> str:
        tier_labels = {"A": "🥇 ELITE", "B": "🥈 STRONG", "C": "🥉 WATCH"}
        tier = tier_labels.get(sig.wallet_tier, "📊")

        # ── Confidence level ──
        if sig.convergence_count >= 3:
            confidence = "🔥🔥🔥 ULTRA HIGH CONVICTION"
        elif sig.convergence_count >= 2:
            confidence = "🔥🔥 HIGH CONVICTION (Multi-Wallet)"
        elif sig.wallet_tier == "A":
            confidence = "🔥 HIGH CONVICTION"
        elif sig.wallet_tier == "B":
            confidence = "⚡ MEDIUM CONVICTION"
        else:
            confidence = "📊 NEW SIGNAL"

        prob_pct = sig.market_probability * 100

        # ── BUY signal: the main product ──
        if sig.side == "BUY":
            # Calculate potential ROI if this bet wins
            if sig.market_probability > 0:
                roi = ((1 / sig.market_probability) - 1) * 100
            else:
                roi = 0

            # Example gains at different investment amounts
            gains_100 = (100 / sig.market_probability) - 100 if sig.market_probability > 0 else 0
            gains_500 = (500 / sig.market_probability) - 500 if sig.market_probability > 0 else 0
            gains_1000 = (1000 / sig.market_probability) - 1000 if sig.market_probability > 0 else 0

            msg = (
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  🚨 NEW BET TIP — {confidence}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"❓ <b>{sig.market_question}</b>\n\n"
                f"👉 BET: <b>{sig.outcome.upper()}</b>\n"
                f"💵 Current price: <b>${sig.market_probability:.2f}</b> ({prob_pct:.0f}%)\n"
                f"📈 Potential ROI if YES: <b>+{roi:.0f}%</b>\n\n"
            )

            # Show what subscribers could make
            msg += "💰 <b>If this bet wins:</b>\n"
            msg += f"  • $100 bet → <b>+${gains_100:,.0f} profit</b>\n"
            msg += f"  • $500 bet → <b>+${gains_500:,.0f} profit</b>\n"
            msg += f"  • $1,000 bet → <b>+${gains_1000:,.0f} profit</b>\n\n"

            # Why we trust this signal
            msg += (
                f"🐋 <b>Smart Money behind this:</b>\n"
                f"  Trader: <code>{sig.wallet_username}</code> [{tier}]\n"
                f"  Their bet: <b>${sig.estimated_usd:,.0f}</b> "
                f"({sig.size_tokens:,.0f} shares)\n"
                f"  💧 Market liquidity: ${sig.market_liquidity:,.0f}\n"
            )

        # ── SELL signal: exit warning ──
        else:
            msg = (
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  ⚠️ EXIT ALERT\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"❓ <b>{sig.market_question}</b>\n\n"
                f"🔴 Smart money is <b>SELLING {sig.outcome.upper()}</b>\n"
                f"💵 Sold at: ${sig.price:.2f} | Amount: ${sig.estimated_usd:,.0f}\n\n"
                f"👤 <code>{sig.wallet_username}</code> [{tier}]\n\n"
                f"⚡ <i>If you hold this position, consider taking profits.</i>\n"
            )

        # ── Convergence highlight ──
        if sig.convergence_count >= 2:
            msg += (
                f"\n🎯 <b>{sig.convergence_count} verified smart wallets</b> "
                f"are making this same bet!\n"
            )

        # ── Link and timestamp ──
        msg += (
            f"\n🔗 <a href='https://polymarket.com/event/{sig.market_slug}'>"
            f"Open on Polymarket</a>\n"
            f"⏰ {datetime.fromtimestamp(sig.timestamp, tz=timezone.utc).strftime('%H:%M UTC')} "
            f"| ⚠️ <i>Not financial advice. DYOR.</i>"
        )
        return msg

    def send_watchlist(self):
        tiers = {"A": [], "B": [], "C": []}
        for sw in self.tracked_wallets.values():
            tiers.get(sw.tier, []).append(sw)

        msg = (
            f"📋 <b>SMART MONEY WATCHLIST UPDATE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Tracking <b>{len(self.tracked_wallets)}</b> verified wallets:\n\n"
        )
        for tier_key, label, emoji in [("A", "ELITE", "🥇"), ("B", "STRONG", "🥈"), ("C", "WATCH", "🥉")]:
            wallets = tiers[tier_key]
            if wallets:
                msg += f"{emoji} <b>{label}</b> ({len(wallets)}):\n"
                for sw in sorted(wallets, key=lambda x: -x.pnl_all)[:5]:
                    msg += (
                        f"  • <code>{sw.username}</code> — "
                        f"WR: {sw.win_rate:.0%} ({sw.closed_positions_count} bets) | "
                        f"ROI: {sw.roi_percent:.0f}% | "
                        f"PnL: ${sw.pnl_all:,.0f}\n"
                    )
                msg += "\n"

        msg += "<i>Signals fire when these wallets make qualifying trades.</i>"
        send_telegram(msg)

    # ── Main refresh cycle ──

    def refresh_wallets(self):
        """Full leaderboard scan → validate → update tracked list."""
        candidates = self.scan_leaderboard()
        if not candidates:
            logger.warning("[SmartMoney] No candidates passed Layers 1+2")
            return

        validated = self.validate_track_records(candidates)
        if validated:
            # Preserve last_seen timestamps from existing wallets
            for addr, sw in validated.items():
                if addr in self.tracked_wallets:
                    sw.last_seen_trade_ts = self.tracked_wallets[addr].last_seen_trade_ts
            self.tracked_wallets = validated
            self.send_watchlist()
            logger.info(f"[SmartMoney] Now tracking {len(self.tracked_wallets)} wallets")
        else:
            logger.warning("[SmartMoney] No wallets passed Layer 3 — keeping previous list")


# ═══════════════════════════════════════════════════════════════════════════════
# FLASK HEALTH CHECK (keeps Render happy)
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)

@app.route("/")
def health():
    return "Polymarket Tipster Bot is running!", 200

@app.route("/status")
def status():
    return {
        "status": "running",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scanner_enabled": MARKET_SCANNER["enabled"],
        "smart_money_enabled": SMART_MONEY["enabled"],
    }, 200


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN BOT LOOP (runs in a background thread)
# ═══════════════════════════════════════════════════════════════════════════════

def bot_main_loop():
    """
    The main loop that runs both engines.
    Runs in a background thread so Flask can serve health checks.
    """
    logger.info("=" * 50)
    logger.info("🚀 POLYMARKET TIPSTER ENGINE STARTING")
    logger.info("=" * 50)

    # Validate credentials
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("❌ TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set!")
        logger.error("   Set them as Environment Variables in Render Dashboard.")
        return

    # Send startup message
    send_telegram(
        "🟢 <b>Polymarket Tipster Bot is online!</b>\n\n"
        f"📊 Market Scanner: {'✅ ON' if MARKET_SCANNER['enabled'] else '❌ OFF'}\n"
        f"🐋 Smart Money Tracker: {'✅ ON' if SMART_MONEY['enabled'] else '❌ OFF'}\n"
        f"⏰ Started at {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )

    scanner = MarketScanner() if MARKET_SCANNER["enabled"] else None
    tracker = SmartMoneyTracker() if SMART_MONEY["enabled"] else None

    last_scanner_run = 0
    last_tracker_refresh = 0
    poll_count = 0

    # Initialize smart money tracker — do the first leaderboard scan
    if tracker:
        logger.info("[SmartMoney] Running initial leaderboard scan...")
        try:
            tracker.refresh_wallets()
            last_tracker_refresh = time.time()
        except Exception as e:
            logger.error(f"[SmartMoney] Initial scan failed: {e}")

    while True:
        try:
            now = time.time()
            poll_count += 1

            # ── Engine 1: Market Scanner ──
            if scanner and (now - last_scanner_run) >= MARKET_SCANNER["scan_interval_hours"] * 3600:
                try:
                    scanner.run_cycle()
                    last_scanner_run = now
                except Exception as e:
                    logger.error(f"[Scanner] Error: {e}")

            # ── Engine 2: Smart Money — refresh leaderboard periodically ──
            if tracker and (now - last_tracker_refresh) >= SMART_MONEY["leaderboard_refresh_hours"] * 3600:
                try:
                    tracker.refresh_wallets()
                    last_tracker_refresh = now
                except Exception as e:
                    logger.error(f"[SmartMoney] Refresh error: {e}")

            # ── Engine 2: Smart Money — poll for new trades ──
            if tracker and tracker.tracked_wallets:
                try:
                    new_signals = tracker.check_new_trades()
                    if new_signals:
                        new_signals = tracker.check_convergence(new_signals)
                        for sig in new_signals:
                            msg = tracker.format_signal(sig)
                            ok = send_telegram(msg)
                            action = "BET TIP" if sig.side == "BUY" else "EXIT ALERT"
                            logger.info(
                                f"[SmartMoney] 📤 {action} SENT: {sig.side} {sig.outcome} on "
                                f"'{sig.market_question[:50]}...' "
                                f"by {sig.wallet_username} (${sig.estimated_usd:,.0f}) "
                                f"[convergence={sig.convergence_count}]"
                            )
                except Exception as e:
                    logger.error(f"[SmartMoney] Trade poll error: {e}")

            # Sleep between polls
            # Heartbeat log every ~15 min so you know the bot is alive
            if poll_count % 15 == 0:
                wallets_n = len(tracker.tracked_wallets) if tracker else 0
                logger.info(
                    f"[Heartbeat] Poll #{poll_count} | "
                    f"Tracking {wallets_n} wallets | "
                    f"Bot uptime: {(now - last_tracker_refresh) / 60:.0f}min since last refresh"
                )

            time.sleep(SMART_MONEY["trade_poll_seconds"] if tracker else 60)

        except Exception as e:
            logger.error(f"Main loop error: {e}")
            time.sleep(60)


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Start the bot loop in a background thread
    bot_thread = threading.Thread(target=bot_main_loop, daemon=True)
    bot_thread.start()

    # Start Flask on the main thread (Render needs this for health checks)
    port = int(os.getenv("PORT", "10000"))
    logger.info(f"Flask health check server starting on port {port}")
    app.run(host="0.0.0.0", port=port)
