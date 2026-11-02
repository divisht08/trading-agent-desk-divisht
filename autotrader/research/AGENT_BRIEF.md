# Brief for strategy research agents (read fully before starting)

Goal: find a long-only NSE equity strategy that beats Nifty 50 buy-and-hold after costs, out of sample, and
holds up in bad years (2008, 2011, 2015-16, 2018, 2020, 2022, 2024-25). An honest "it does not beat Nifty"
is a valid, useful result. Never massage numbers.

## Environment
- Work only in /home/claude/research. Run python from that directory: `cd /home/claude/research && python3 your_script.py`.
- Shared data: data/panel.npz (all NSE equities 2004 - 23 Oct 2026, daily OHLCV, split/bonus adjusted,
  NOT dividend adjusted). Load with `harness.load_panel()`. Nifty 50 index closes: `harness.load_nifty()`.
- DO NOT edit harness.py, data/, AGENT_BRIEF.md, or other agents' files. Do not use git, do not push, do not
  contact the user. Do not download anything (except the intraday agent, per its own instructions).
- Machine: 2 CPU cores shared by ~6 agents running at once. Vectorise with pandas/numpy over the whole panel;
  no Python loops over dates x symbols. Keep each evaluate() run under ~3 minutes and limit total runs.

## Strategy contract (see harness.py and strategies/example_momentum.py)
`weights(panel, univ, **params) -> DataFrame` (dates x symbols), weight >= 0, row sum <= 1 (rest is cash).
Weight at date t may use only data up to the close of t. The harness trades it at the next open, holds
open-to-open, and charges 0.14% per side (brokerage + statutory + slippage). Use `harness.hold_from_month_end`
for monthly rebalancing. `univ` = point-in-time top-100 most liquid stocks (by median traded value), which
removes most survivorship bias. You may build a different universe with `harness.liquid_universe(panel, top_n=...)`
if your idea needs it - declare it.

## Required procedure
1. Write strategies/<name>.py with the weights() function and a docstring explaining the logic and the
   evidence behind it (cite papers or well-known practitioner sources by name/year).
2. Declare a SMALL parameter grid (<= 12 combinations) BEFORE looking at results. Write it in the file.
3. Write run_<name>.py that calls
   `harness.evaluate("<name>", weights, grid, out_dir="results/<name>", notes="...")`.
   evaluate() picks params by IN-SAMPLE (2008-2019) Sharpe only, reports out-of-sample 2020-2026, full cycle,
   every bad period, double costs, the whole grid's OOS (robustness) and a look-ahead check.
   Never choose parameters by out-of-sample results. If you change the idea after seeing results, say so
   in the report and count it as a new trial (max 3 trials total).
4. The look-ahead check must pass. If it fails, fix the bug.
5. Write results/<name>/REPORT.md: logic, rationale, chosen params, `harness.summary_table(res)`, grid
   robustness, turnover/costs, and an honest verdict: does it beat Nifty out of sample (2020-2026) after
   costs, and how did it do in each bad period? List caveats.

## Context already known (2008 - Oct 2026, costs included)
- Nifty 50 buy & hold: 7.48%/yr, Sharpe 0.47, max drawdown -59.9%.
- Equal-weight top-100 liquid (monthly): 6.27%/yr, Sharpe 0.37, max drawdown -69.1%.
- Top-5 momentum (6-1 month) on the point-in-time top-100: 3.4-3.9%/yr, Sharpe ~0.27, max DD -65 to -79%.
  (The same on today's 30 large caps showed 14.6%/yr - that was survivorship bias.)
- A breadth regime filter (cash when < 50% of universe above 200-day EMA) cut drawdowns on large caps.

## Final message (your reply to the coordinator)
<= 200 words: strategy, chosen params, OOS CAGR/Sharpe/max DD vs Nifty, full-cycle numbers, bad-period
summary, look-ahead check result, verdict, and file paths.
