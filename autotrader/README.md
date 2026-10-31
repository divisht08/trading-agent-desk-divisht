# Autotrader (NSE, Kite Connect)

A separate, self-contained agent that lives alongside Agent Desk in this repo. It shares no code with
`backend/`, `ml/` or `frontend/`, and the two are run and deployed separately.

| | Agent Desk (repo root) | Autotrader (this folder) |
|---|---|---|
| Decides trades with | rule score + gradient-boosted model + Claude analyst/bull/bear/PM debate | two fixed rule strategies; Claude can only veto (optional, off by default) |
| Horizons | daily-bar swing (with intraday square-off) | intraday 5-minute breakouts **and** multi-day/week trend trades |
| Backtest role | model validated walk-forward; the debate is judged from Test mode | a strategy cannot trade at all unless it passes an out-of-sample gate |
| Data | free Kite MCP, or paid Kite Connect | Kite Connect data plan (historical API needed) |
| Dashboard | React app | minimal HTML page + JSON |


Autonomous long-only NSE equity agent: intraday (MIS) opening-range breakouts plus multi-day/multi-week (CNC) trend breakouts, with a hard risk layer. Runs on a server, no laptop needed. **Starts in paper mode, and a strategy trades only after passing a walk-forward backtest.**

How each trade is set up, and every piece of information used: [docs/HOW_TRADES_ARE_MADE.md](docs/HOW_TRADES_ARE_MADE.md).

## Architecture

```
agent/
  app.py        FastAPI service + trading thread: /login, /callback, / (dashboard), /pause, /resume, /state
  engine.py     tick loop: manage exits -> check limits -> scan entries -> (optional LLM veto) -> order
  strategies.py intraday_orb, swing_trend (+ trailing exit). Strategies only emit signals.
  risk.py       sizing + hard limits. Every entry must pass RiskManager.approve()
  broker.py     PaperBroker (live prices, simulated fills) | KiteBroker (real orders, locked by default)
  research.py   optional Claude headline check that can veto a trade (fails closed)
  backtest.py           daily-bar backtester (swing) + Nifty buy-and-hold benchmark
  backtest_intraday.py  5-minute-bar backtester (intraday)
  validate.py           walk-forward test and go/no-go gate -> validation.json
  fetch_history.py      downloads history from Kite Connect into data/
  store.py              SQLite: positions, trades, events, token
config.yaml     all parameters (set `capital` here); validated parameters override strategy defaults
docs/           how trades are made
```

## Run

All commands below run from inside `autotrader/`.


1. Zerodha: create a Kite Connect app at developers.kite.trade (paid monthly subscription; check the current price there). Set the redirect URL to `https://<your-server>/callback`.
2. Copy `.env.example` to `.env` and fill in `KITE_API_KEY`, `KITE_API_SECRET`, `DASH_KEY`, `PUBLIC_URL`
   (optional: Telegram bot for the daily login link, `ANTHROPIC_API_KEY` for the LLM veto).
3. Edit `capital` in `config.yaml`.
4. Run the backtest gate (section below) and copy the resulting `validation.json` into the `/data` volume.
   Without it the service runs but no strategy trades.
5. Deploy anywhere that runs Docker (small VPS, Fly.io, Railway, Render), mounting a volume at `/data`:
   `cd autotrader && docker build -t autotrader . && docker run -d --env-file .env -p 8000:8000 -v autotrader-data:/data autotrader`
6. **Daily:** from 08:00 IST each weekday the agent sends the login link once (Telegram, or open `/login` yourself). Approve on your phone; it trades until close.
   Zerodha requires this daily login; storing your password/TOTP to bypass it is against their terms and is not implemented.
7. Dashboard: `https://<server>/?key=<DASH_KEY>`. Kill switch: `POST /pause?key=...` (entries stop; exits still run) and `/resume`.

## Default risk rules (config.yaml)

0.75% of current equity (capital + realized P&L) risked per trade, max 6 positions, max 20% of equity in one stock,
40% intraday / 70% swing exposure caps, max 12 entries a day. A realized loss of 2% in a day or 5% in a week stops new
entries and closes intraday positions (unrealized P&L is not counted). Intraday entries only 09:30–14:45, all intraday
positions squared off at 15:12, every position has a stop.

## Strategy logic (why each rule exists)

Full detail, including data inputs and what is not used: [docs/HOW_TRADES_ARE_MADE.md](docs/HOW_TRADES_ARE_MADE.md).

**Swing trend breakout (CNC, days–weeks).** Trend-following/momentum is widely documented in equity research
(Jegadeesh & Titman 1993; Moskowitz, Ooi & Pedersen 2012); the backtest gate checks whether it holds here after costs. Rules: buy when yesterday's close breaks the N-day high, *and* EMA50 > EMA200
(uptrend), *and* close > EMA50, *and* volume >= k x 20-day average (participation), *and* not >15% above EMA50
(not chasing). Stop = entry − m x ATR(14) (volatility-scaled, so noisy stocks get wider stops and smaller size).
Exit: trailing stop from the highest close, a close below EMA50 (trend broken), or max holding period. Decisions use the
previous day's completed candle and execute at market after 09:20 (backtest: next day's open).

**Intraday opening-range breakout (MIS).** The first 15–30 minutes form the day's balance area; the first close above
it on expanded volume and above VWAP indicates buyers in control. Stop below the range (capped by ATR), target =
R-multiple of risk from the actual fill, square-off 15:12, at most one intraday trade per stock per day, no chasing
beyond 1.5 ATR. Only completed 5-minute candles are used.

**Sizing.** Every trade risks a fixed % of current equity between entry and stop, so a loss is bounded and similar in size
regardless of the stock; position, exposure and loss-limit caps sit on top.

## Backtesting and the go/no-go gate (mandatory)

The live engine **will not trade a strategy** unless `validation.json` shows it passed. No file = nothing trades.

1. `KITE_API_KEY=... KITE_ACCESS_TOKEN=... python -m agent.fetch_history` — ~8 years of daily and ~400 days of
   5-minute bars for the universe, plus Nifty 50 daily, into `data/`.
2. `python -m agent.validate` — walk-forward test:
   - first 70% of history = in-sample: a small, pre-declared parameter grid is tried, the best Sharpe is picked;
   - last 30% = out-of-sample: that one parameter set is run on data it never saw;
   - every grid point is also run out-of-sample: if only one setting works, the edge is treated as luck.
   - Costs (brokerage + statutory + slippage) on every fill; next-bar fills; stop-before-target inside a bar.
3. Gate (fixed in `agent/validate.py` before seeing any results):
   - swing: >=30 OOS trades, profit factor >=1.2, max drawdown better than −20%, Sharpe >= Nifty buy-and-hold,
     >=50% of grid profitable OOS;
   - intraday: >=50 OOS trades, PF >=1.2, avg R >=0.10, drawdown better than −10%, Sharpe >=1, >=50% of grid profitable.
4. Passing strategies are enabled with their validated parameters. The service re-reads `validation.json` at each
   daily login, so re-running the gate monthly and replacing the file switches off a strategy that stops passing.
   Not simulated in backtests: the news veto, the weekly loss halt, and (swing only) the daily halts and trade-count limit.
5. Then paper trade 4–8 weeks and compare live paper results with the backtest before going live.

Backtests can still overfit and markets change; a pass is permission to paper trade, not a profit guarantee.

## Going live (deliberately two-step)

1. Set `mode: live` in `config.yaml`.
2. Set env `LIVE_UNLOCK=I_ACCEPT_REAL_MONEY_RISK`.
Without both, `KiteBroker` refuses to start. Start with a small capital. Live cash is capped at `min(account balance, capital)`.

Before going live, also see the root README's "Going from Test to Automatic": API orders must come from a static IP
registered with Kite, and check with Zerodha how SEBI's retail-algo framework applies to your setup.

## Known limits

- Long-only, cash equity, market orders. Stops are enforced by the agent loop (every `tick_seconds`), not by exchange-side orders, so a crash or gap can exceed the stop. Add GTT/SL-M orders before scaling capital.
- Positions are tracked in SQLite; a startup reconciliation against broker positions is not yet implemented. Do not trade the same account manually in these symbols while live.
- The optional news veto is not backtested (see docs).
- No real-market backtest results are included in this repo yet; run the gate yourself before trading.

Not investment advice. You are responsible for all trades and losses.
