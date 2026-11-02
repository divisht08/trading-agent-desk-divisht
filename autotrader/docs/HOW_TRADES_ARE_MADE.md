# How a trade is set up: the hybrid decision pipeline

The agent is a **hybrid**: deterministic, backtested rules decide *what* to trade; an optional
language-model check can only *veto*; a hard-coded risk layer decides *how much*, or vetoes too.
No component can bypass the risk layer, and nothing trades without a passing backtest.

```
 market data ─▶ 1. Backtest gate ─▶ 2. Strategy skills ─▶ 3. Risk manager ─▶ 4. News check ─▶ 5. Execution
 (Kite)          validation.json      emit Signals          size or reject     (optional veto)   paper | live
                                                                                                   │
                                                          6. Position management (stops, trail, square-off) ◀┘
```

## 1. Backtest gate (`agent/validate.py`, `Engine.apply_validation`)

Before the day starts, the engine reads `validation.json`. A strategy is switched on only if its
walk-forward, out-of-sample backtest passed the fixed gate rules, and it then runs with the parameters
that passed. Missing file, failed result or unreadable file = strategy off. See README for the method.

## 2. Strategy skills (`agent/strategies.py`)

Each "skill" is a pure function: market data in, a `Signal` (symbol, product, entry, stop, target,
reason) or nothing out. The same functions run in the backtester and live.

| Skill | Horizon | Product | Fires when | Stop | Exit |
|---|---|---|---|---|---|
| `intraday_orb` | minutes–hours | MIS | first completed 5-min close above the opening range, volume ≥ k × 20-bar average, above session VWAP, ≤ 1.5 ATR past the range | below the range, no further than `atr_stop_mult` × 5-min ATR, at least 0.5 ATR | stop, target (`target_r` × risk from the actual fill), or square-off at 15:12 |
| `swing_trend` | days–weeks | CNC | yesterday's close > prior N-day high, EMA50 > EMA200, close > EMA50, volume ≥ k × 20-day average, close < 1.15 × EMA50 | entry − `atr_stop_mult` × daily ATR(14) | stop (checked every tick), trailing stop from the highest close, close below EMA50, or `max_hold_days` |

Why these rules:
- **Trend/momentum** (swing): stocks that have been rising tend to keep rising over weeks to months.
  This is widely documented (Jegadeesh & Titman 1993 for cross-sectional momentum; Moskowitz, Ooi &
  Pedersen 2012 for time-series momentum). Whether it still holds for these stocks, after Indian
  trading costs, is exactly what the backtest gate checks.
- **Opening-range breakout** (intraday): the first 15–30 minutes absorb overnight news and set a
  range; a decisive break on higher volume, above the volume-weighted average price, shows buyers in
  control. This is a practitioner rule, not an academic result, so its gate is stricter.
- **Volume, VWAP and EMA filters** remove breakouts with no participation or against the trend.
- **ATR-based stops** scale to each stock's normal movement, so a stop is not hit by ordinary noise.
- **No chasing** filters skip entries that are already far past the trigger level.

## 3. Risk manager (`agent/risk.py`)

Every Signal passes `RiskManager.approve()`. It rejects or shrinks the trade:

- size = (risk % × current equity) ÷ (entry − stop), so every loss at the stop costs about the same;
- caps: one stock ≤ 20% of equity, MIS total ≤ 40%, CNC total ≤ 70%, ≤ 6 open positions,
  ≤ 12 entries per day, price between ₹50 and ₹20,000, no doubling up in a stock already held;
- halts: realized loss ≥ 2% today or ≥ 5% this week → no new entries and intraday positions closed;
- cash: never spends more than available cash (live: the lower of the account balance and `capital`).

"Current equity" = configured `capital` + realized P&L. Unrealized P&L is not counted in the loss limits.

## 4. News check (`agent/research.py`, optional, off by default)

If `research.llm_veto: true`, the agent fetches up to 8 Google News headlines from the last 2 days for
the stock and asks Claude whether there is a fresh negative catalyst (fraud, regulatory action,
earnings miss, downgrade, promoter selling, litigation, sector shock). Claude can only **block** a
trade, never create or enlarge one. On any error the trade is blocked (fails closed).

This step is **not backtested**: there is no point-in-time headline archive to replay without
lookahead. Its effect is unmeasured, which is why it is off by default. If you turn it on, compare
paper results with it on and off before relying on it.

## 5. Execution (`agent/broker.py`)

- **Paper** (default): live Kite prices; fills at the reference price ± slippage; brokerage and
  statutory charges deducted on each side, using the same cost rates as the backtester.
- **Live**: market orders on NSE through Kite Connect; the agent waits up to ~20 s for a fill and
  cancels if unfilled. Locked unless `mode: live` **and** `LIVE_UNLOCK` are both set.

## 6. Position management (`agent/engine.py`)

Every `tick_seconds` (60 s) during market hours: intraday stops/targets/square-off are checked;
swing stops are checked live, and the trailing-stop / EMA / max-hold decision is made once per day
after 09:20 on the previous day's completed candle, then executed at market. Only completed candles
are used for decisions, live and in backtests.

## Information the agent uses

| Input | Source | Used by |
|---|---|---|
| Last traded price | Kite Connect `ltp` | entries, stops, targets, paper fills |
| 5-minute OHLCV, last ~6 days | Kite Connect historical | `intraday_orb` (range, VWAP, ATR, volume) |
| Daily OHLCV, last ~420 calendar days | Kite Connect historical | `swing_trend`, swing exits (EMA50/200, ATR14, N-day high, volume) |
| Account balance | Kite Connect `margins` (live only) | cash cap |
| Recent headlines | Google News RSS (optional) | news veto |
| Nifty 50 daily history | Kite Connect historical | backtest benchmark only |
| Trading universe | `config.yaml` (30 liquid NSE large caps) | all scans |

## Not used (deliberately, for now)

Company fundamentals (earnings, valuations, balance sheets), analyst ratings, F&O data, options flow,
short selling, sector rotation, and machine-learning predictions. None of these can be backtested
with the data this project currently downloads; adding any of them should come with point-in-time
history and a gate test of its own.
