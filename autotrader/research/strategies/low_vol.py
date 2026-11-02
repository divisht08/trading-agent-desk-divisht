"""low_vol: defensive low-volatility portfolio with an optional 52-week-high proximity filter.

Logic (monthly rebalance, long only, point-in-time top-100 liquid universe from harness.liquid_universe):
  1. For every stock in the universe at month-end t, estimate volatility as the standard deviation of
     daily close-to-close returns over the last `vol_lookback` trading days (data up to close t only;
     |daily move| > 40% is treated as a data error and dropped, matching the harness).
  2. Optional trend filter: keep only stocks whose close is >= `high52_min` x their 252-day high
     (George & Hwang 2004 "52-week high and momentum investing"). high52_min = 0 disables it.
  3. Hold the `top_n` lowest-volatility survivors. Weights are inverse-volatility (as in the
     Nifty 100 Low Volatility 30 index methodology) or equal. If fewer than top_n pass the filter,
     only those are held at the same per-name size budget (1/top_n each on average) and the rest
     is cash - so the 52w-high filter also acts as a soft regime filter in broad sell-offs.

Evidence relied on (well-known, cited by name/year; no local copy of the papers):
  - Blitz & van Vliet (2007), "The volatility effect: lower risk without lower return" (JPM).
  - Baker, Bradley & Wurgler (2011), "Benchmarks as limits to arbitrage: understanding the
    low-volatility anomaly" (FAJ).
  - Frazzini & Pedersen (2014), "Betting against beta" (JFE) - the low-risk premium across markets.
  - Ang, Hodrick, Xing & Zhang (2006) on low idiosyncratic volatility.
  - George & Hwang (2004), "The 52-week high and momentum investing" (JF).
  - NSE Indices, Nifty 100 Low Volatility 30 methodology: 30 least volatile of the Nifty 100 by
    1-year daily-return std, weighted by inverse volatility, rebalanced quarterly (practitioner source;
    we rebalance monthly to use the harness helper).

Pre-declared parameter grid (written before any results were seen) - 10 combinations:
  top_n in {20, 30} x vol_lookback in {126, 252} x high52_min in {0.0, 0.8}, inverse-vol weighting  (8)
  + equal-weight controls: top_n=30, vol_lookback=252, high52_min in {0.0, 0.8}                      (2)
"""
import numpy as np
import pandas as pd

from harness import hold_from_month_end

GRID = (
    [{"top_n": n, "vol_lookback": lb, "high52_min": h, "weighting": "inv_vol"}
     for n in (20, 30) for lb in (126, 252) for h in (0.0, 0.8)]
    + [{"top_n": 30, "vol_lookback": 252, "high52_min": h, "weighting": "equal"} for h in (0.0, 0.8)]
)


def weights(panel, univ, top_n=30, vol_lookback=252, high52_min=0.0, weighting="inv_vol"):
    # work only on symbols that are ever in the universe (per-column rolling ops, so this is lossless)
    cols = univ.columns[univ.any(axis=0).values]
    u = univ[cols].reindex(panel.dates).fillna(False).astype(bool)
    c = panel.close[cols].astype("float64")
    r = c.pct_change(fill_method=None)
    r = r.where(r.abs() <= 0.40)
    vol = r.rolling(vol_lookback, min_periods=int(vol_lookback * 0.8)).std()
    elig = u & vol.notna() & (vol > 0)
    if high52_min > 0:
        hi = panel.high[cols].astype("float64").rolling(252, min_periods=200).max()
        elig &= (c >= high52_min * hi)
    v = vol.where(elig)
    rank = v.rank(axis=1, ascending=True, method="first")
    pick = rank <= top_n
    if weighting == "inv_vol":
        raw = (1.0 / v).where(pick)
        # scale so a full basket of top_n names sums to 1: normalise by the mean inverse-vol of the
        # picked names x top_n, so a short basket (filter removed names) leaves the remainder in cash
        n_pick = pick.sum(axis=1)
        tgt = raw.div(raw.sum(axis=1).replace(0, np.nan), axis=0).mul(n_pick / top_n, axis=0)
    else:
        tgt = pick.astype(float) / top_n
    tgt = tgt.fillna(0.0)
    return hold_from_month_end(tgt)
