# Parallel strategy research: results (2 Nov 2026)

Six research agents each designed and backtested one strategy family **in parallel** on one shared harness;
a seventh, independent agent audited the five daily strategies. Everything below is after costs
(0.14% per side), long-only, price-only (no dividends on either side), ₹ terms irrelevant (weights).

**Rules every agent followed** ([AGENT_BRIEF.md](AGENT_BRIEF.md)): parameter grid of ≤ 12 combinations declared
before running; parameters chosen on **in-sample 2008–2019 Sharpe only**; judged **out of sample 2020–Oct 2026**,
over the full cycle and in every bad period; automatic look-ahead check by re-running on truncated data.

## Leaderboard

| Strategy | Full 2008–26 (CAGR / Sharpe / max DD) | Out of sample 2020–26 | Audit verdict | Beats Nifty? |
|---|---|---|---|---|
| **Nifty 50 buy & hold** | 7.18% / 0.45 / −59.9% | 9.52% / 0.61 / −38.4% | – | – |
| Equal-weight top-100 liquid | 6.27% / 0.37 / −69.1% | 13.61% / 0.70 / −40.1% | – | – |
| **Low volatility** (20 least-volatile of top-100, inverse-vol, monthly) | **10.39% / 0.67 / −52.1%** | **11.40% / 0.76 / −28.7%** | Pass with caveats | **Yes, every stress test** |
| Momentum+ (top-10, 12-1 month, inverse-vol) | 8.39% / 0.44 / −72.5% | 11.51% / 0.54 / −40.7% | Pass with caveats | Not robustly (fails at 2× costs, −72% DD) |
| Breakout v2 (252-day high entry, 50-day low exit, regime) | 4.85% / 0.50 / −26.5% | 6.02% / 0.55 / −19.9% | Pass with caveats | No (7.8% / Sharpe 0.76 if idle cash earns 6%) |
| Trend timing (top-50 basket, cash in downtrends) | 2.27% / 0.22 / −45.7% | 10.07% / 0.67 / −19.1% | Pass with caveats | No |
| Mean reversion (RSI(2) dips in uptrends) | 1.55% / 0.18 / −56.7% | −5.58% / −0.15 / −52.5% | Strategy fails | No — costs eat ~9.8%/yr |
| Intraday ORB (5-min, 15 stocks, real minute data 2017–20) | −58% (2017–18) | −58% (2019–20) | Not audited (agent report) | No — costs eat 0.75–0.9 R per trade |

## Bad periods: return % (max drawdown %)

| Period | Nifty | Low vol | Momentum+ | Breakout v2 | Trend timing | Mean reversion |
|---|---|---|---|---|---|---|
| 2008 crisis | −51.8 (−59.9) | −43.3 (−52.1) | −66.6 (−72.5) | **−12.7 (−14.4)** | −36.7 (−40.1) | −36.5 (−42.9) |
| 2011 slowdown | −24.6 (−26.2) | −15.8 (−16.3) | −15.5 (−21.1) | **−8.2 (−9.0)** | −16.6 (−21.2) | −14.7 (−18.8) |
| 2015–16 correction | −21.0 (−22.5) | −14.1 (−16.5) | −14.1 (−24.5) | −13.5 (−15.7) | −15.1 (−18.0) | −13.7 (−22.7) |
| 2018 mid/small-cap crash | **+3.2** (−14.6) | +1.3 (−15.4) | −14.9 (−20.7) | −10.3 (−14.6) | −22.7 (−30.9) | −14.8 (−25.4) |
| 2020 COVID year | 14.9 (−38.4) | **27.2** (−28.7) | 13.8 (−40.7) | 1.3 (−15.8) | 9.6 (−16.5) | −31.2 (−41.0) |
| 2020 crash (Jan–Mar) | −30.5 (−38.4) | −21.1 (−28.7) | −28.4 (−40.7) | −12.7 (−15.8) | **−10.7 (−11.2)** | −34.7 (−39.4) |
| 2022 rate hikes | 4.3 (−16.5) | 3.7 (−18.2) | **6.1** (−29.3) | −6.9 (−14.9) | 2.0 (−14.5) | −19.3 (−25.6) |
| 2024–25 correction | **−10.3** (−15.7) | −11.8 (−17.7) | −24.7 (−31.4) | −14.0 (−14.3) | −10.4 (−12.3) | −21.9 (−24.8) |

## Independent audit ([audit/AUDIT.md](audit/AUDIT.md))

- **No look-ahead found; every number reproduced exactly.** Execution timing unit-tested (decide at close t →
  trade at open t+1); universe recomputed on truncated data with zero differences; extra mid-month truncation tests passed.
- **Survivorship bias remains (serious).** The data source drops delisted companies, so only 9 of 2,426 stocks ever stop
  trading: Satyam, DHFL, Jet Airways, HDFC Ltd, merged PSU banks and other failures are missing. Choosing the universe by
  liquidity at each date does **not** fix this. Every strategy above is biased upward versus Nifty, by an unknown amount;
  strategies that hold risky, volatile names (momentum, breakout) are likely flattered more than low volatility.
- **Costs ignore drift:** the simulator rebalances to target daily at no cost; a drift-aware re-run takes ~1 pp/yr off the
  monthly strategies (low vol: 10.39% → 9.40% full cycle, still above Nifty).
- Low volatility survives every stress: +1/+2-day delay, 2× and 3× costs (3×: 9.18% full / 10.10% OOS), drift-aware
  simulation, and zeroing its five biggest contributors (7.41% full / 9.49% OOS, ≈ Nifty).
- Trend timing's choice rested on an in-sample Sharpe of 0.01 — effectively a coin toss.

## Verdict

1. **Low volatility is the only candidate.** It beat Nifty by ~3 pp/yr over 18 years and ~2 pp/yr out of sample, with a
   smaller drawdown, and was the most robust to every stress. Its edge is modest and partly inflated by survivorship bias;
   its main reliable benefit is lower risk. This matches published evidence (low-volatility anomaly; Nifty 100 Low
   Volatility 30 index).
2. **Everything else is rejected or unproven**: momentum (not robust), breakout and trend timing (lower drawdowns but lower
   returns), mean reversion and intraday ORB (destroyed by costs).
3. Next: re-test low volatility on a survivorship-free dataset (needs delisted-stock history, e.g. NSE bhavcopy archives or
   a paid vendor), then add it to the live engine as a monthly rebalanced, paper-traded strategy.
