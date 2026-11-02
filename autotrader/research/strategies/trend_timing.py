"""Market trend timing of a broad large-cap basket (`trend_timing`).

Logic
-----
1. Basket: the point-in-time top-N (N = 30 or 50) most liquid NSE stocks by 60-day median traded value
   (`harness.liquid_universe(panel, top_n=N)`, re-selected each month-end), equal-weighted and rebalanced
   monthly. This is an index-like large-cap basket that the harness can trade.
2. Exposure: fully invested in the basket when the market is in an uptrend, 100% cash otherwise.
   The "market" is the Nifty 50 index (`harness.load_nifty()`), aligned to the panel calendar.

Evidence
--------
- Faber (2007), "A Quantitative Approach to Tactical Asset Allocation": hold an asset when its month-end
  price is above its 10-month simple moving average, otherwise cash. Similar equity returns with much
  smaller drawdowns across US and international indices.
- Moskowitz, Ooi & Pedersen (2012), "Time Series Momentum" (JFE): an asset's own past 12-month (and
  shorter) excess return predicts its next-month return across 58 futures markets, including equity indices.
- Siegel (1994/"Stocks for the Long Run") and many practitioners use the 200-day moving average daily;
  daily checks react faster but whipsaw more (each round trip costs 0.28% here).
- Breadth (share of stocks above their 200-day average) is a common practitioner regime filter; the
  coordinator's earlier test showed it cut drawdowns on large caps.

Data note (declared before running)
-----------------------------------
The Nifty file starts 2007-09-17 and ends 2026-04-13, so a 200-day or 12-month signal would not exist
for most of 2008 and would freeze after April 2026. The market series is therefore a CHAINED return
series: the Nifty daily return where Nifty has a close on both t-1 and t, otherwise the daily close-to-close
return of the equal-weight top-50 basket computed from the panel. Each value uses data up to t only, so
the series is causal. All signals are computed on this chained index.

Parameter grid (declared before looking at results, 12 combinations)
--------------------------------------------------------------------
basket_n in {30, 50} x signal in:
  "sma200_daily"   index close > 200-day SMA, checked every day
  "sma200_monthly" same rule, checked only at month-end
  "sma10m"         month-end close > average of the last 10 month-end closes (Faber), monthly
  "tsmom12"        12-month index return > 0 (MOP 2012), monthly
  "tsmom6"         6-month index return > 0, monthly
  "combo"          at least 2 of 3 true at month-end: sma10m, tsmom12, breadth >= 50%
                   (breadth = share of the top-100 universe above its 200-day SMA)

Trial 2 (declared AFTER seeing trial 1, counted as a second trial): trial 1's equal-weight basket lost
~2%/yr in 2008-2019 while Nifty made +5.9%/yr, so the timing had a poor asset to time. Trial 2 keeps the
same 12-point signal grid but weights the basket by 60-day median traded value at each month-end (a rough
stand-in for Nifty's free-float cap weighting), capped at 10% per stock with the excess redistributed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import harness

SIGNALS = ("sma200_daily", "sma200_monthly", "sma10m", "tsmom12", "tsmom6", "combo")
# Trial 1 (equal-weight basket)
GRID = [{"basket_n": n, "signal": s} for n in (30, 50) for s in SIGNALS]
# Trial 2 (declared after trial 1, see REPORT.md): same signals, traded-value-weighted basket capped at 10%
GRID_TV = [{"basket_n": n, "signal": s, "weighting": "tv_capped"} for n in (30, 50) for s in SIGNALS]

_UNIV_CACHE: dict = {}


def _basket_universe(panel, n: int) -> pd.DataFrame:
    key = (n, len(panel.dates), panel.dates[-1], panel.close.shape[1])
    if key not in _UNIV_CACHE:
        _UNIV_CACHE[key] = harness.liquid_universe(panel, top_n=n)
    return _UNIV_CACHE[key]


def market_index(panel) -> pd.Series:
    """Chained market index on the panel calendar: Nifty return when available, else the
    equal-weight top-50 basket's close-to-close return. Uses data up to t only."""
    dates = panel.dates
    b = _basket_universe(panel, 50)
    c = panel.close
    rc = c / c.shift(1) - 1
    rc = rc.where(rc.abs() <= harness.MAX_DAILY_MOVE)
    held = b.shift(1, fill_value=False) & rc.notna()          # members known at t-1 close
    proxy = rc.where(held).mean(axis=1).fillna(0.0)
    n = harness.load_nifty()
    n = n[n.index <= dates[-1]].reindex(dates)
    nr = n / n.shift(1) - 1
    r = nr.where(n.notna() & n.shift(1).notna(), proxy)
    return (1 + r).cumprod()


def _monthly_flag(daily_bool: pd.Series) -> pd.Series:
    """Sample a daily signal at month-ends and hold it through the next month."""
    m = harness.month_end_mask(daily_bool.index)
    return daily_bool.where(m.values).ffill().fillna(False).astype(bool)


def _month_end_series(idx: pd.Series) -> pd.Series:
    return idx[harness.month_end_mask(idx.index).values]


def exposure(panel, univ, signal: str) -> pd.Series:
    idx = market_index(panel)
    dates = panel.dates
    sma200 = idx.rolling(200, min_periods=200).mean()
    up200 = (idx > sma200)
    me = _month_end_series(idx)
    sma10m = (me > me.rolling(10, min_periods=10).mean())
    ts12 = (me / me.shift(12) - 1) > 0
    ts6 = (me / me.shift(6) - 1) > 0
    if signal == "sma200_daily":
        flag = up200
    elif signal == "sma200_monthly":
        flag = _monthly_flag(up200)
    elif signal in ("sma10m", "tsmom12", "tsmom6", "combo"):
        if signal == "sma10m":
            m = sma10m
        elif signal == "tsmom12":
            m = ts12
        elif signal == "tsmom6":
            m = ts6
        else:
            c = panel.close
            ma = c.rolling(200, min_periods=200).mean()
            above = (c > ma).where(univ & ma.notna())
            breadth = above.sum(axis=1) / (univ & ma.notna()).sum(axis=1).replace(0, np.nan)
            br = (breadth >= 0.5).reindex(me.index)
            m = (sma10m.astype(int) + ts12.astype(int) + br.astype(int)) >= 2
        flag = m.reindex(dates).ffill().fillna(False).astype(bool)
    else:
        raise ValueError(signal)
    return flag.astype(float)


def _capped(w: pd.DataFrame, cap: float, iters: int = 20) -> pd.DataFrame:
    """Row-normalise, then cap each weight at `cap` and redistribute the excess pro rata (iterated)."""
    w = w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    for _ in range(iters):
        over = w > cap
        if not over.to_numpy().any():
            break
        excess = (w - cap).clip(lower=0).sum(axis=1)
        free = w.where(~over, 0.0)
        fs = free.sum(axis=1).replace(0, np.nan)
        w = w.clip(upper=cap) + free.div(fs, axis=0).mul(excess, axis=0).fillna(0.0)
    return w


def weights(panel, univ, basket_n: int = 50, signal: str = "sma10m", weighting: str = "equal",
            cap: float = 0.10) -> pd.DataFrame:
    b = _basket_universe(panel, basket_n)
    if weighting == "equal":
        ew = b.astype(float).div(b.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    elif weighting == "tv_capped":
        tv = (panel.close * panel.volume).rolling(60, min_periods=48).median()
        ew = _capped(tv.where(b).fillna(0.0), cap)
    else:
        raise ValueError(weighting)
    ew = harness.hold_from_month_end(ew)                      # monthly rebalance of the basket
    return ew.mul(exposure(panel, univ, signal), axis=0)
