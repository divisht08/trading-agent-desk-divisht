# Independent audit of five NSE strategy backtests

Auditor: independent (did not write the strategies). Date: 2026-11-02. Data: data/panel.npz (2004-01-01 .. 2026-10-23).
Scripts: `audit/a1_data.py` (panel, Nifty, extreme moves), `audit/a2_trend_signals.py` (trend_timing signals),
`audit/a3_harness_tests.py` (timing unit test, universe point-in-time, data quality), `audit/audit_strategy.py <name>`
(reproduction, stress, drift-aware sim, cash yield, concentration, mid-month look-ahead check; output `audit/out_<name>.json`),
`audit/make_tables.py` (tables below, `audit/tables.txt`). No existing file was modified.

Benchmarks as computed by the harness (price index, no dividends; strategies are also price-only):
Nifty 50 full 2008-26 **7.18%/yr**, Sharpe 0.45, max DD -59.9%; OOS 2020-26 **9.52%/yr**, Sharpe 0.61, max DD -38.4%.
Equal-weight top-100 (same survivor-biased panel): full 6.27%, OOS **13.61%**, Sharpe 0.70.
(The brief's 7.48% Nifty figure predates the Nifty extension to Oct 2026; 2026 YTD is -13.5%.)

## 1. Harness findings  — verdict: PASS WITH CAVEATS (one serious data caveat)

1. **Execution timing: correct.** Unit test (a3): a weight of 1.0 decided at close 2015-06-10 earns exactly
   open(06-11)->open(06-12) (-2.98% both ways), with buy cost on 06-11 and sell cost on 06-12. `held = w.shift(1)`
   (harness.py:117) pairs with `r` labelled t = open t -> open t+1 (harness.py:115). A weight decided at t never earns
   open(t)->open(t+1). No off-by-one.
2. **Turnover/costs: correct for target changes, but drift is ignored** (harness.py:113-119). Turnover is
   |w_t - w_t-1| of *targets*; between rebalances the harness implicitly rebalances every position back to its target
   weight every day at zero cost. I wrote a drift-aware simulator (shares held between trades, costs on real trades).
   Effect on full-cycle CAGR: low_vol -1.0 pp, momentum_plus -1.0 pp, trend_timing -0.7 pp, mean_reversion -0.8 pp,
   breakout_v2 +0.2 pp (trend followers gain from letting winners run). This inflates the monthly strategies by ~1 pp/yr.
   0.14%/side is slightly light for Indian delivery trades (STT 0.1% each side + stamp + exchange + GST + DP charge +
   slippage is ~0.15-0.17%); the 2x stress covers it.
3. **liquid_universe is point-in-time** (harness.py:89-104): recomputed on panels truncated at 2012-06-29, 2018-03-28 and
   2023-09-29, zero differences. **But the panel itself is survivor-biased — the most important finding.** Only 9 of 2,426
   symbols stop trading before 2026; 0 stop before 2015; of the 449 stocks ever in the top-100 only 3 ever stop trading.
   The eod2 source README states "Delisted stocks are auto-removed after 1 year of inactivity. It is not meant for
   backtesting", and its own ISIN map lists 1,134 securities that stopped trading 2011-2025, none present in the panel
   (build_panel.py:9-15 keeps only symbols that still have a CSV). Missing liquid names include HDFC Ltd, Ranbaxy, Satyam,
   DHFL, Jet Airways, Kingfisher, Cairn India, Sesa Goa, Sterlite, IDFC, Bhushan Steel, Reliance Capital, Indiabulls Real
   Estate, Punj Lloyd, Mindtree, LTI and the merged PSU banks; symbols renamed before 2011 lose their earlier history
   (AXISBANK starts 2007-08). The brief's claim that the PIT universe "removes most survivorship bias" is false. All
   strategy results (and the EW top-100 benchmark) are biased upward versus the unbiased Nifty benchmark; the size cannot
   be measured with the data on disk. Strategies that buy falling or high-beta names (mean_reversion, momentum, breakout)
   are the most exposed; low_vol the least.
4. **Look-ahead check blind spots** (harness.py:179-191): (a) `univ` is precomputed on the full panel and only sliced, so
   a leak inside `liquid_universe` would never be caught (I verified it separately: none); (b) external data loaded inside
   `weights()` (e.g. `load_nifty()`) is not truncated unless the strategy does it (trend_timing does, trend_timing.py:81);
   (c) all three cuts are month-ends, so month-end logic is never tested on a partial month; (d) only the chosen params
   are checked. Rolling windows with min_periods, ffill, cross-sectional ranks and full-sample normalisation would all be
   caught. My stronger check (five mid-month cuts, compare every weight strictly before the cut day) **passes for all five
   strategies** (max difference <= 3e-17). On the cut day itself monthly strategies differ because `month_end_mask`
   treats the last available day as a month-end; that is a live-trading artefact, not a backtest leak.
5. **MAX_DAILY_MOVE** (harness.py:27,116): 38 in-universe open-to-open moves above 40% (since 2005). Most are unadjusted
   demergers or splits (ADANIENT 2015-06-03 -80%, BHARTIARTL 2009-07-23 -52%, TATACHEM 2020-03-03 -56%, VEDL 2026-04-29
   -61%, SIEMENS 2025-04-04 -54%, TMPV 2025-10-13 -41%); for a demerger, zeroing is roughly right because holders get the
   new shares. But it also zeroes real moves: YESBANK 2020-03-06 -48.7% (held by none of the five) and ABAN +42%,
   RELINFRA +41% (held by trend_timing, which hurts it). Moves of 25-40% (140 in universe) are kept, including some
   one-day spike-and-reverse pairs (ABB 2008-10-24/27). Net effect on the five chosen strategies is negligible. Dividends
   are not adjusted in the panel or in Nifty, so comparisons are consistent; low_vol's higher-yield names are slightly
   understated.
6. **Nifty benchmark: clean.** The two sources agree to 4.8e-8 over 3,459 overlapping days; there are no gaps over 6
   calendar days and never more than 1 consecutive zero-return day (4 in total); no flat stretches 2008-2026. 26 panel
   dates (holidays, Muhurat sessions) have no Nifty close: they are forward-filled (0 return, the move lands the next day),
   which is harmless. Note: `benchmark_returns` uses close-to-close Nifty against open-to-open strategies (a timing
   mismatch of under a day, not a bias).
7. Procedure: none of the five results folders contains the REPORT.md that the brief requires.

## 2. Per-strategy results

Cells are CAGR % / Sharpe / max DD %. "Cash earns 6%" adds (1 - exposure) x 6%/252 per day to the published
daily_returns.csv. "Top-5 zeroed" sets the 5 biggest contributors' returns to 0 (a harsh test that would hurt any portfolio).

### low_vol — PASS WITH CAVEATS
Params {top_n 20, vol_lookback 126, no 52w filter, inv_vol}. The grid has 10 points; the chosen one has the best in-sample
Sharpe (0.62). Reproduced exactly (daily returns match to 1e-16).

| Variant | Full 2008-26 CAGR / Sharpe / MaxDD | OOS 2020-26 CAGR / Sharpe / MaxDD |
|---|---|---|
| Reported | 10.39 / 0.67 / -52.12 | 11.4 / 0.76 / -28.68 |
| Reproduced | 10.39 / 0.67 / -52.12 | 11.4 / 0.76 / -28.68 |
| +1 day delay | 10.3 / 0.66 / -52.01 | 11.47 / 0.77 / -28.67 |
| +2 day delay | 10.61 / 0.68 / -52.02 | 11.6 / 0.78 / -28.97 |
| 2x costs (0.28%/side) | 9.78 / 0.64 / -52.35 | 10.75 / 0.73 / -28.72 |
| 3x costs (0.42%/side) | 9.18 / 0.6 / -52.57 | 10.1 / 0.69 / -28.75 |
| +1 day delay and 2x costs | 9.69 / 0.63 / -52.25 | 10.82 / 0.73 / -28.7 |
| Drift-aware sim (full) | 9.4 / 0.62 / -53.7 | 10.99 / 0.74 / -28.63 |
| Cash earns 6%/yr | 10.39 / 0.67 / -52.12 | 11.4 / 0.76 / -28.68 |
| Top-5 contributors zeroed | 7.41 / 0.56 / -49.85 | 9.49 / 0.74 / -26.19 |

- No look-ahead: `low_vol.py:43` selects columns ever in the universe using the full sample, but everything after it is
  per-column or within-row, so this loses nothing (the mid-month cuts confirm it). Weights sum to <= 1.
- Robust to delay and to 3x costs. The drift-aware sim costs 1.0 pp full / 0.4 pp OOS. It beats Nifty in every variant
  (full 9.2-10.6 vs 7.18, OOS 10.1-11.6 vs 9.52) but not the EW top-100 on OOS CAGR (13.6). Top-5 contributors (HUL,
  Sun Pharma, Cipla, TCS, ITC) are 26% of the gross contribution; without them it is 7.4% full, still above Nifty.
- Caveats: survivorship (step 1.3), the drift effect, the 2008 drawdown of -52% (worst year -43%).

### momentum_plus — PASS WITH CAVEATS (code clean; the edge over Nifty is not robust)
Params {top_n 10, lookback 252, regime none}. Grid 12; chosen = best in-sample Sharpe (0.38). Reproduced exactly.

| Variant | Full 2008-26 CAGR / Sharpe / MaxDD | OOS 2020-26 CAGR / Sharpe / MaxDD |
|---|---|---|
| Reported | 8.39 / 0.44 / -72.49 | 11.51 / 0.54 / -40.7 |
| Reproduced | 8.39 / 0.44 / -72.49 | 11.51 / 0.54 / -40.7 |
| +1 day delay | 7.48 / 0.41 / -70.81 | 11.25 / 0.53 / -41.12 |
| +2 day delay | 7.12 / 0.4 / -70.81 | 11.89 / 0.55 / -41.15 |
| 2x costs (0.28%/side) | 7.02 / 0.39 / -72.7 | 10.01 / 0.49 / -40.77 |
| 3x costs (0.42%/side) | 5.66 / 0.34 / -72.92 | 8.53 / 0.44 / -40.83 |
| +1 day delay and 2x costs | 6.12 / 0.36 / -71.04 | 9.75 / 0.48 / -41.19 |
| Drift-aware sim (full) | 7.37 / 0.4 / -73.35 | 11.53 / 0.54 / -40.78 |
| Cash earns 6%/yr | 8.39 / 0.44 / -72.49 | 11.51 / 0.54 / -40.7 |
| Top-5 contributors zeroed | 4.42 / 0.3 / -72.49 | 9.6 / 0.48 / -40.7 |

- No leak found (`mom = c.shift(skip)/c.shift(lookback)`, momentum_plus.py:52; the regime proxy uses same-day universe
  returns, momentum_plus.py:64). The one-day delay costs 0.9 pp full, almost all from 2009 (+19.7 pp in that year
  alone): one crash-rebound month-end, which is noise, not a leak.
- The full-cycle Sharpe (0.44) is never above Nifty's (0.45) in any variant. Max DD is -72%. 3x costs give 5.7% full; the
  drift-aware sim gives 7.4% full, which is Nifty-like. Top-5 zeroed gives 4.4% full. This style is the most exposed to
  survivorship (momentum names that later collapsed, such as DHFL in 2018, are missing).
- Stale docstring (momentum_plus.py:19-20) says Nifty ends 2026-04-13; it now runs to 2026-10-23. Harmless.

### breakout_v2 — PASS WITH CAVEATS
Params {entry 252, exit 50, regime_mode entries}. Grid 12; chosen = best in-sample Sharpe (0.47). Reproduced exactly.

| Variant | Full 2008-26 CAGR / Sharpe / MaxDD | OOS 2020-26 CAGR / Sharpe / MaxDD |
|---|---|---|
| Reported | 4.85 / 0.5 / -26.49 | 6.02 / 0.55 / -19.92 |
| Reproduced | 4.85 / 0.5 / -26.49 | 6.02 / 0.55 / -19.92 |
| +1 day delay | 5.1 / 0.52 / -25.47 | 5.92 / 0.53 / -20.26 |
| +2 day delay | 5.07 / 0.52 / -27.33 | 6.01 / 0.54 / -19.31 |
| 2x costs (0.28%/side) | 4.52 / 0.47 / -27.2 | 5.61 / 0.52 / -20.45 |
| 3x costs (0.42%/side) | 4.19 / 0.44 / -27.9 | 5.19 / 0.48 / -20.98 |
| +1 day delay and 2x costs | 4.77 / 0.49 / -26.17 | 5.5 / 0.5 / -20.79 |
| Drift-aware sim (per_name) | 5.07 / 0.46 / -29.7 | 6.82 / 0.54 / -22.39 |
| Cash earns 6%/yr | 7.79 / 0.76 / -22.29 | 8.42 / 0.74 / -16.97 |
| Top-5 contributors zeroed | 3.08 / 0.36 / -25.03 | 3.54 / 0.37 / -21.21 |

- No leak: the state machine at t uses only signals at t (breakout_v2.py:76-97); the exit level is the prior-day N-low
  (`.shift(1)`, line 64). Insensitive to delay. Max row sum 0.94.
- Average exposure is 53% (OOS 62%). Without cash yield it trails Nifty on CAGR (4.85 vs 7.18 full, 6.02 vs 9.52 OOS).
  With 6% on idle cash it is 7.8% full / 8.4% OOS, with Sharpe 0.76/0.74 and DD -22%/-17% (Nifty 0.45/0.61, -60%/-38%).
  So it gives better risk-adjusted returns but no higher absolute return.
- Concentrated: the top 5 (TVSMOTOR, ADANIENT, AUROPHARMA, RECLTD, BAJAJ-AUTO) are 32% of the gross contribution;
  without them it is 3.1% full / 3.5% OOS.
- The docstring (lines 15-16) says weights are "fixed ... so no churn costs", but the harness re-targets daily. The
  drift-aware sim is slightly *better* (5.07 full, 6.82 OOS).

### trend_timing — PASS WITH CAVEATS (no leak; it does not beat Nifty over the full cycle)
Params {basket_n 50, signal combo}. Grid 12 (trial 1), plus a 12-point trial 2 (tv_capped, in-sample Sharpe -0.07, OOS 8.28%,
correctly not chosen): 24 configurations in all. Chosen = best in-sample Sharpe, which is **0.01** (in-sample CAGR -1.9%).
Reproduced exactly.

| Variant | Full 2008-26 CAGR / Sharpe / MaxDD | OOS 2020-26 CAGR / Sharpe / MaxDD |
|---|---|---|
| Reported | 2.27 / 0.22 / -45.72 | 10.07 / 0.67 / -19.08 |
| Reproduced | 2.27 / 0.22 / -45.72 | 10.07 / 0.67 / -19.08 |
| +1 day delay | 1.63 / 0.18 / -46.99 | 9.6 / 0.64 / -19.12 |
| +2 day delay | 1.67 / 0.18 / -47.9 | 10.43 / 0.69 / -18.34 |
| 2x costs (0.28%/side) | 1.83 / 0.19 / -46.67 | 9.62 / 0.65 / -19.67 |
| 3x costs (0.42%/side) | 1.39 / 0.17 / -49.07 | 9.18 / 0.62 / -20.25 |
| +1 day delay and 2x costs | 1.19 / 0.16 / -49.46 | 9.16 / 0.62 / -19.37 |
| Drift-aware sim (full) | 1.58 / 0.18 / -46.1 | 9.92 / 0.66 / -19.35 |
| Cash earns 6%/yr | 4.0 / 0.31 / -42.48 | 11.72 / 0.76 / -17.5 |
| Top-5 contributors zeroed | 1.05 / 0.15 / -44.8 | 8.63 / 0.63 / -18.53 |

- The selection is essentially noise: every in-sample Sharpe is between -0.16 and 0.01. The OOS result (10.07 vs Nifty
  9.52) is a small edge that shrinks to 9.2-9.9 under delay, costs or drift; full cycle 2.3% vs 7.18%.
- `sma200_monthly` and `sma10m` are effectively the same rule: they agree on 273 of 274 month-ends (a2;
  trend_timing.py:100-109), so the grid has ~10 distinct points, not 12.
- The chained Nifty/proxy index is causal and is truncated to the panel (trend_timing.py:80-83). The docstring note
  (line 25) about Nifty ending 2026-04-13 is stale.
- 5 held moves above 40% were zeroed, including real positive moves (ABAN +42%, RELINFRA +41%) and demergers. Tiny net
  effect (2% weights).

### mean_reversion — PASS (honest numbers; the strategy itself fails)
Params {rsi_entry 5, max_pos 5, regime False}. Grid 8; chosen = best in-sample Sharpe (0.37). Reproduced exactly.

| Variant | Full 2008-26 CAGR / Sharpe / MaxDD | OOS 2020-26 CAGR / Sharpe / MaxDD |
|---|---|---|
| Reported | 1.55 / 0.18 / -56.74 | -5.58 / -0.15 / -52.54 |
| Reproduced | 1.55 / 0.18 / -56.74 | -5.58 / -0.15 / -52.54 |
| +1 day delay | -0.87 / 0.07 / -66.44 | -11.17 / -0.46 / -61.26 |
| +2 day delay | -5.72 / -0.18 / -70.66 | -11.54 / -0.51 / -57.84 |
| 2x costs (0.28%/side) | -7.73 / -0.25 / -79.41 | -14.81 / -0.62 / -69.64 |
| 3x costs (0.42%/side) | -16.17 / -0.68 / -96.46 | -23.14 / -1.09 / -84.0 |
| +1 day delay and 2x costs | -9.93 / -0.37 / -87.46 | -19.86 / -0.94 / -79.42 |
| Drift-aware sim (per_name) | 0.72 / 0.14 / -57.23 | -5.86 / -0.17 / -52.69 |
| Cash earns 6%/yr | 4.28 / 0.3 / -50.73 | -3.36 / -0.05 / -48.73 |
| Top-5 contributors zeroed | -1.23 / 0.06 / -61.58 | -7.69 / -0.26 / -56.15 |

- No leak found (RSI and SMA on closes at t; entry at the next open). It is very sensitive to timing and cost: one day of
  delay turns full-cycle CAGR negative, and 2x costs give -7.7%/yr, because one-way turnover is 34x/yr. OOS is negative
  in every variant. It is the strategy most likely to be flattered further by survivorship: dip-buying in names that
  later failed (DHFL, Jet) cannot happen in this panel.
- Its exposure to unadjusted corporate actions (an ex-dividend or demerger drop looks like an RSI dip): ADANIENT
  2015-06-03 was held at 20% weight through a -80% demerger move that the harness zeroed.

## 3. Bottom line
- No look-ahead or off-by-one in the harness or any of the five strategies; all reported numbers reproduce exactly, and
  the chosen params match the best in-sample Sharpe in every case (grids <= 12).
- Systematic upward biases common to all: (1) the survivor-biased panel (unmeasured, likely the largest effect);
  (2) free daily rebalancing to target (~1 pp/yr for the monthly strategies).
- Only low_vol beats Nifty in every stress variant (full and OOS). breakout_v2 beats it only on risk-adjusted terms once
  idle cash earns interest. momentum_plus and trend_timing do not show a robust edge. mean_reversion fails.
