# Agent Desk

A self-hosted trading desk for Zerodha Kite. A multi-agent Claude debate (after
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)) and a gradient-boosted
model screen NSE/BSE stocks, decide trades and place them, in **Test** mode (simulated orders on
live prices) or **Automatic** mode (real orders, switched on with two-step verification).

```
backend/   FastAPI API, trading engine, Kite gateway, brokers, scheduler, security
ml/        signals, ML features + model + registry, backtester, Claude decision desk (prompts in .md files)
frontend/  React + TypeScript dashboard (Vite)
```

## What it does each cycle

1. **Kite session**: checks today's login, and texts you a login link if it's missing.
2. **Universe**: NSE index constituents with sectors, matched to Kite's instrument list.
3. **Market data**: live quotes and daily history from Kite.
4. **Signals + model**: a rule score (trend, momentum vs Nifty, MACD, RSI, breakouts) blended with
   the model's probability that the next 5 days beat trading costs.
5. **Agent debate**: for the strongest candidates and every held stock, an analyst (with today's news via
   Claude web search), a bull and a bear argue, and a portfolio manager decides.
6. **Risk check**: sizing, max positions and any limits you set.
7. **Orders**: simulated in Test mode, real Kite orders in Automatic mode.
8. **Portfolio**: books updated. In Automatic mode the live book is checked against Kite.

The dashboard shows each step live. You can see the trade ideas and debates, bullish and bearish
stocks, sectors, orders and positions, run backtests, view model metrics and use controls, on desktop
or phone.

## Modes

| | Test | Automatic |
|---|---|---|
| Orders | Simulated at live prices with Zerodha charges and slippage | Real Kite MARKET orders with market protection, tagged `kagent` |
| Money | `PAPER_CAPITAL` test book | `LIVE_CAPITAL` live book (only what the agent bought) |
| Switch on | Always available | Server switch + budget + Kite login + **password + authenticator code** |
| Switch off | – | One tap on **Test**, at any time |

Automatic mode refuses to arm unless every check passes:
- `LIVE_TRADING_ENABLED=true` and `LIVE_CAPITAL` are set on the server;
- real Kite data is in use (not demo) and you're logged in to Kite today;
- the dashboard password is set and the authenticator app is set up (Controls);
- you type the password **and** a fresh 6-digit code from the authenticator app.

Codes can't be reused, and five wrong tries lock the switch for 15 minutes. With
`AUTO_REVERIFY_DAILY=true`, Automatic drops back to Test at 06:00 every day.

## What it costs to run

| Piece | Cost |
|---|---|
| Market data (Test and Automatic) | **Free** with `DATA_MODE=kite_mcp`: Zerodha's hosted Kite MCP, the same connector Claude chat uses |
| Placing real orders (Automatic only) | **Free** with a Kite Connect **Personal** API key |
| Kite Connect paid data plan | Optional (₹500/month): set `DATA_MODE=kite` if the MCP is ever unavailable or rate-limited |
| Claude API (the debate) | Pay per use, capped by `CLAUDE_DAILY_BUDGET_USD` (default $2/day). Without a key the rule set decides, at no cost |
| Server | A small VPS with a static IP |

Zerodha doesn't publish terms for server-side use of the hosted MCP, so it could change or be rate-limited.
That is why the paid data plan stays a one-line switch.

To keep Claude costs down, a stock is debated again only when its score moves by
`DEBATE_SCORE_DELTA` points, its holding changes, or `DEBATE_REFRESH_MIN` minutes have passed.
Otherwise the last debate is reused, and the dashboard says so.

## Try it now (no Kite account needed)

```bash
make install
make demo        # http://localhost:8000, password: demo
```

Demo mode uses synthetic prices, every screen says so, and real orders are impossible in it.

## Run it on Windows

1. Install the tools once (PowerShell):
   ```powershell
   winget install Python.Python.3.12 OpenJS.NodeJS.LTS Git.Git
   ```
   Close and reopen PowerShell afterwards.
2. Get the code and try the demo:
   ```powershell
   git clone https://github.com/divisht08/trading-agent-desk-divisht
   cd trading-agent-desk-divisht
   powershell -ExecutionPolicy Bypass -File scripts\start-windows.ps1 -Demo
   ```
   The first run takes a few minutes while it installs. Your browser opens http://localhost:8000. The password is `demo`.
3. Use your real Kite data in Test mode: run the script without `-Demo`. It creates `.env` and opens it in
   Notepad. Set `DASHBOARD_PASSWORD`, save, and run it again. Sign in, open **Controls**, choose **Log in here**
   and log in to Kite. The agent runs every 15 minutes during market hours while the window stays open.
4. After pulling new code, add `-Rebuild`.

The laptop has to stay on and awake for scheduled cycles. For hands-off running every day, use a server (below).

## Run it for real

1. **Kite**: Test mode needs nothing beyond your Zerodha login, because data comes free through the Kite MCP.
   For Automatic mode later, create a free **Personal** app at developers.kite.trade and set its Redirect URL to
   `https://YOUR-DOMAIN/kite/callback`.
2. **Server**: a small always-on Linux VPS with a **static IP**. Exchange rules require a registered
   static IP for API orders, so register it in the Kite developer console before using Automatic mode.
3. **Configure**: `cp .env.example .env` and fill it in. For Test mode set `PUBLIC_URL`,
   `DASHBOARD_PASSWORD`, `SECRET_KEY` and one phone channel (`NTFY_TOPIC` or Telegram). Add
   `ANTHROPIC_API_KEY` for the Claude debate.
4. **Start**: put your domain in `Caddyfile`, then `docker compose up -d --build`.
5. **Each morning**: a login link arrives on your phone at 08:45. Tap it, log in on Kite's page, and the
   desk runs by itself until the close. In Automatic mode with free data, a second link logs in the
   order API. If the server restarts, the MCP needs a fresh login, and the desk sends a link.
6. **Model**: press **Train now** in the Model section once you're logged in. After that it retrains
   every Friday after the close.

## Going from Test to Automatic

Run Test mode until the record is worth trusting. A reasonable bar is 6–8 weeks and 40+ closed trades,
ahead of Nifty after charges, with a drawdown you'd accept with real money. Then:

1. Register the server's static IP with Kite.
2. Ask Zerodha how they treat AI-generated orders from your own API app under SEBI's retail-algo
   framework (circular of 4 Feb 2025, NSE standards of 5 May 2025). Self-built strategies under
   10 orders/second need no exchange registration, but confirm for your case.
3. Add your free Personal `KITE_API_KEY`/`KITE_API_SECRET`, set `LIVE_TRADING_ENABLED=true` and a small
   `LIVE_CAPITAL`, restart, and set up the authenticator under Controls.
4. Consider `MAX_POSITION_PCT`, `DAILY_LOSS_LIMIT_PCT` and `STOP_LOSS_PCT` first. They're off by default.
5. Switch to **Automatic** in the top bar.

## Scope

- NSE and BSE cash equities: everything Kite lists as an equity. `UNIVERSE_INDEX=all` scans every
  NSE equity trading ₹10 cr+ a day.
- US stocks can't be traded through Kite Connect, whose exchanges are NSE, BSE, NFO, BFO, CDS, BCD and
  MCX. NSE-listed ETFs that track US indices work like any other stock (`EXTRA_SYMBOLS`).

## The ML part

- **Features**: 25 inputs per stock per day (returns, distance from averages, RSI, MACD, volatility,
  volume, 52-week position, relative strength vs Nifty, the rule score).
- **Label**: did the next-day-open to 5-day-close return beat about 0.25% round-trip costs?
- **Model**: scikit-learn `HistGradientBoostingClassifier`, pooled across the universe.
- **Validation**: a time-ordered holdout with a 5-day purge, reporting AUC, top-decile hit rate and
  lift. Backtests of the model are walk-forward: re-trained every 63 days on past data only.
- **Use**: new models only switch on above `ML_MIN_AUC` and when they're not worse than the current
  one. The model's share of the score is `ML_WEIGHT`.

The Claude debate can't be backtested fairly, because the model already knows how past prices played
out. Judge it from Test-mode results.

## Autotrader (separate agent)

[`autotrader/`](autotrader/README.md) is a second, independent agent: intraday opening-range breakouts and
multi-week trend trades, a hard risk layer, and a walk-forward backtest gate that must pass before a strategy
can trade. It shares no code with the desk above. How it sets up trades: [autotrader/docs/HOW_TRADES_ARE_MADE.md](autotrader/docs/HOW_TRADES_ARE_MADE.md).

## Development

```bash
make test       # ml + backend pytest, frontend vitest + typecheck
make dev-api    # API on :8000
make dev-web    # React on :5173 with /api proxied
```

See [AGENTS.md](AGENTS.md) for the rules every change must keep, especially around real orders and
look-ahead.

Nothing here is investment advice. No strategy, including this one, is guaranteed to make money.
