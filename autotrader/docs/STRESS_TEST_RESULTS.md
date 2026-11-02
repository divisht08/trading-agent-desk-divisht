# Stress test: bad market years (run 2 Nov 2026)

Daily-bar backtests of each strategy variant through India's bad periods, against Nifty 50 buy-and-hold.
Reproduce with `python -m agent.stress_test <daily_dir> <nifty_csv>`.

**Setup.** Capital ₹1,00,000. Untuned default parameters from `config.yaml` (not optimised on these
periods). Costs and slippage on every fill, next-day-open fills, no look-ahead. Universe: the 30 stocks
in `config.yaml`. Stock data: NSE daily reports via the public `eod2_data` repository (split/bonus
adjusted, not dividend adjusted). Nifty: index closes (cross-checked against a second source: identical).
The intraday strategy is **not** included: no 5-minute history for these years.

Variants:
- **Swing (as built)**: 20-day breakout in an uptrend, ATR stop, trailing exit.
- **Swing + ranking + min size**: competing signals ordered by momentum; trades shrunk below 25% of
  intended size are skipped.
- **+ regime**: no new swing entries when fewer than 50% of the universe trade above their 200-day EMA.
- **Momentum rotation (top 5)**: at each month-end, hold the 5 stocks with the best return from ~6 months
  to ~1 month ago (positive only), equal weight.
- **Momentum rotation + regime**: same, but all cash when breadth is below 50%.

## Return in each bad period (%), max drawdown in brackets

| Period | Nifty | Swing | Swing + rank + min size | Swing + regime | Momentum | Momentum + regime |
|---|---|---|---|---|---|---|
| 2008 crisis | −51.8 (−59.9) | −2.3 (−2.9) | −2.3 (−2.9) | −2.0 (−2.5) | −33.7 (−42.5) | **0.0 (0.0)** |
| 2011 slowdown | −24.9 (−26.2) | −10.3 (−10.4) | −10.3 (−10.4) | −7.1 (−7.4) | −2.9 (−13.9) | **−1.1 (−10.8)** |
| 2015-16 correction | −22.0 (−22.5) | −15.9 (−15.9) | −15.9 (−15.9) | −10.4 (−10.4) | −7.2 (−17.0) | **−1.7 (−15.1)** |
| 2018 mid/small-cap crash | **4.0** (−14.6) | −1.8 (−5.2) | −1.3 (−4.9) | −1.9 (−5.5) | −13.2 (−20.9) | −15.2 (−21.3) |
| 2020 COVID year | 14.8 (−38.4) | 13.3 (−3.3) | 13.3 (−3.3) | 12.2 (−3.7) | 0.2 (−43.2) | **43.5 (−8.6)** |
| 2020 crash only (Jan–Mar) | −30.3 (−38.4) | −0.7 (−3.2) | −0.6 (−3.2) | −1.0 (−2.9) | −34.0 (−41.7) | **2.1 (−7.9)** |
| 2022 rate hikes | 2.7 (−16.5) | −3.2 (−9.3) | −6.2 (−11.7) | −5.8 (−10.1) | **5.0** (−17.9) | −0.8 (−12.1) |
| 2024-25 correction | −10.2 (−15.7) | −10.7 (−12.3) | −10.6 (−12.3) | −11.8 (−12.3) | **−8.5** (−15.6) | −11.8 (−15.2) |

## Full cycle 2008 – Oct 2026 (good and bad years)

| | Total return | Per year | Max drawdown | Sharpe |
|---|---|---|---|---|
| Nifty buy & hold | +288% | 7.5% | −59.9% | 0.47 |
| Swing (as built) | −35% | −2.2% | −38.5% | −0.28 |
| Swing + rank + min size + regime | −21% | −1.2% | −27.0% | −0.14 |
| Momentum rotation | +1,052% | 13.9% | −44.7% | 0.71 |
| **Momentum rotation + regime** | **+1,266%** | **14.9%** | **−29.5%** | **0.88** |

## What this means

1. **The swing breakout strategy fails.** It sidesteps crashes (−2% in 2008, −1% in the 2020 crash)
   because the uptrend filter keeps it out, but it bleeds in choppy markets and loses money over the full
   cycle. The backtest gate would switch it off. Ranking and minimum size barely change it.
2. **Momentum rotation with the regime filter is the only variant worth pursuing.** It went to cash
   before the 2008 and 2020 crashes (traced: cash from the end of Feb 2020 to the end of Jul 2020) and
   roughly halved Nifty's worst drawdown over 18 years.
3. **It still has bad years.** It lost 15% in 2018 while Nifty rose 4%, and lost 12% in the 2024-25
   correction, slightly worse than Nifty. The regime filter reacts at month-end, so slow grinding
   declines catch it.

## Why the momentum numbers are too good (read before trusting them)

- **Survivorship bias.** The universe is *today's* 30 large caps. Stocks that collapsed or fell out of
  the large-cap list since 2008 are not in it, and some of today's names (e.g. Bajaj Finance) were small
  in 2008 and grew precisely because they kept winning. Momentum picks recent winners, so this bias
  flatters it more than it flatters the index. The real edge is smaller than shown.
- No dividends on either side (roughly cancels; Nifty total return would be ~1.3%/yr higher).
- One fixed parameter set; not yet walk-forward validated or tested on a broader, point-in-time universe.

## Next steps

1. Re-run momentum on a broad, point-in-time universe (all liquid NSE stocks at each date, including
   later-delisted ones) to remove most of the survivorship bias.
2. Walk-forward validation through `agent/validate.py`, with the gate rules unchanged.
3. Only if it still beats Nifty: add momentum rotation to the live engine and paper trade it.
