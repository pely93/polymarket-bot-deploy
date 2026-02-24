# Polymarket Smart Money Tipster Bot

A production-ready Telegram bot that combines two signal engines:

1. **Market Scanner** — finds high-probability Polymarket bets based on price, volume, and liquidity
2. **Smart Money Tracker** — monitors the sharpest leaderboard wallets and alerts when they make new trades

## Why Your Previous Deploy Failed

Your original code had a race condition: Flask and the async bot loop competed for the main thread, and Render's health check wasn't being answered in time. This version fixes that by:

- Running the bot loop in a **background thread**
- Running Flask on the **main thread** (answers Render's health check immediately)
- Using `gunicorn` with a `post_fork` hook to start the bot after the web worker is ready
- Removing the `python-telegram-bot` async dependency entirely (uses plain `requests` for Telegram, which is simpler and more reliable for this use case)

## Files

| File | Purpose |
|------|---------|
| `bot.py` | The entire bot — both engines, Telegram sender, Flask health check |
| `gunicorn_conf.py` | Auto-starts the bot thread when gunicorn launches |
| `requirements.txt` | Python dependencies |
| `render.yaml` | One-click Render deployment config |
| `.env.example` | Template for local environment variables |

## Deploy to Render

### Step 1: Push to GitHub

```bash
git init
git add .
git commit -m "Polymarket tipster bot"
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

### Step 2: Create Render Web Service

1. Go to [render.com](https://render.com) → New → Web Service
2. Connect your GitHub repo
3. Render will detect `render.yaml` and auto-configure
4. **Add these Environment Variables** in the Render dashboard:
   - `TELEGRAM_BOT_TOKEN` — get from [@BotFather](https://t.me/BotFather)
   - `TELEGRAM_CHAT_ID` — your channel ID (see below)
5. Click Deploy

### Step 3: Get Your Telegram Chat ID

**For a public channel:** Use `@YourChannelName`

**For a private channel:**
1. Add your bot to the channel as an admin
2. Send any message in the channel
3. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
4. Look for `"chat":{"id":-100XXXXXXXXXX}` — that's your chat ID

## Local Development

```bash
cp .env.example .env
# Edit .env with your credentials
pip install -r requirements.txt
python bot.py
```

## Tuning the Filters

All thresholds are in the `MARKET_SCANNER` and `SMART_MONEY` config dicts at the top of `bot.py`.

### Market Scanner — key knobs:

| Setting | Default | Effect |
|---------|---------|--------|
| `min_probability` | 65% | Lower = more markets, riskier |
| `max_probability` | 92% | Higher = includes near-certain bets (low ROI) |
| `min_volume` | $10k | Lower = more obscure markets |
| `min_liquidity` | $5k | Lower = harder for subscribers to enter |

### Smart Money Tracker — key knobs:

| Setting | Default | Effect |
|---------|---------|--------|
| `min_pnl_all_time` | $5k | Raise for only elite traders |
| `min_win_rate` | 54% | Raise for higher quality, fewer wallets |
| `min_trade_size_usd` | $200 | Raise to only see large conviction bets |
| `convergence_window_minutes` | 60 | Longer = catches more convergence but may be stale |

### Recommended profiles:

**Conservative** (fewer, higher-quality tips):
```
min_pnl_all_time = 10000, min_win_rate = 0.60, min_trade_size_usd = 500
```

**Balanced** (good starting point — the defaults):
```
min_pnl_all_time = 5000, min_win_rate = 0.54, min_trade_size_usd = 200
```

**Aggressive** (more signals):
```
min_pnl_all_time = 2000, min_win_rate = 0.52, min_trade_size_usd = 100
```
