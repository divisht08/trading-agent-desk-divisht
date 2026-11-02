"""momentum_plus: diversified, risk-adjusted cross-sectional momentum with crash protection.

Logic (decided at each month-end close, held for the month, traded next open by the harness):
  1. Universe: the harness's point-in-time top-100 liquid NSE stocks (no survivorship).
  2. Score = risk-adjusted momentum: total return from t-`lookback` to t-21 (skip the last month,
     short-term reversal; Jegadeesh & Titman 1993) divided by the stock's 126-day daily volatility.
     Only stocks with positive raw momentum qualify (absolute-momentum screen).
  3. Hold the top `top_n` (10 or 20, vs the naive 5) with inverse-volatility weights (more names and
     vol-weighting cut idiosyncratic risk, which dominated the naive top-5 version).
  4. Crash protection (`regime`):
       "none"      - always invested.
       "trend"     - cash when the market proxy is below its 200-day simple average (Faber 2007 style
                      trend filter; momentum crashes cluster in bear markets / rebounds, Daniel &
                      Moskowitz 2016).
       "trend_vt"  - trend filter plus volatility targeting: exposure = min(1, 15% / realised 126-day
                      vol of the un-scaled strategy) (Barroso & Santa-Clara 2015, "Momentum has its
                      moments"; also Moreira & Muir 2017).
     Market proxy: an equal-weighted close-to-close index of the point-in-time top-100 universe built from
     the panel. Declared deviation: harness.load_nifty() only covers 2007-09-17 .. 2026-04-13, so a Nifty
     200-day average would be missing in H1 2008 and stale after April 2026; the panel proxy covers the
     whole test window. Everything uses data up to the close of t only.

Evidence: Jegadeesh & Titman (1993); Barroso & Santa-Clara (2015); Daniel & Moskowitz (2016);
Indian evidence: Agarwalla, Jacob & Varma (2014, IIM-A working paper "Four factor model in Indian equities
market", data library) report a positive WML (12-1 momentum) premium on NSE/BSE; Sehgal & Balakrishnan
(2002, Vikalpa) find momentum profits in Indian stocks. Practitioner: NSE's Nifty200 Momentum 30 index
uses volatility-adjusted 6m/12m momentum (NSE methodology document, 2020).

Parameter grid (declared before any results were seen): top_n in {10, 20} x lookback in {126, 252}
x regime in {"none", "trend", "trend_vt"} = 12 combinations.
"""
import numpy as np
import pandas as pd

GRID = [{"top_n": n, "lookback": lb, "regime": rg}
        for n in (10, 20) for lb in (126, 252) for rg in ("none", "trend", "trend_vt")]


def _hold_monthly(target: pd.DataFrame) -> pd.DataFrame:
    from harness import hold_from_month_end
    return hold_from_month_end(target)


def weights(panel, univ, top_n=10, lookback=252, skip=21, vol_window=126, regime="trend",
            trend_window=200, target_vol=0.15):
    c = panel.close
    univ = univ.reindex(index=c.index, columns=c.columns).fillna(False).astype(bool)
    d = c.pct_change(fill_method=None)
    d = d.where(d.abs() <= 0.40)                                   # treat >40% daily moves as data errors
    vol = d.rolling(vol_window, min_periods=int(vol_window * 0.8)).std() * np.sqrt(252)

    mom = c.shift(skip) / c.shift(lookback) - 1
    score = (mom / vol).where(univ & (mom > 0) & (vol > 0))
    rank = score.rank(axis=1, ascending=False, method="first")
    pick = rank <= top_n
    iv = (1.0 / vol).where(pick)
    target = iv.div(iv.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    w = _hold_monthly(target)

    if regime == "none":
        return w

    # market proxy: equal-weight daily return of the point-in-time universe
    mkt_r = d.where(univ).mean(axis=1).fillna(0.0)
    mkt = (1 + mkt_r).cumprod()
    sma = mkt.rolling(trend_window, min_periods=trend_window).mean()
    risk_on = (mkt > sma) | sma.isna()                             # no filter until 200 days exist
    scale = risk_on.astype(float)

    if regime == "trend_vt":
        strat_r = (w.shift(1) * d.fillna(0.0)).sum(axis=1)          # un-scaled strategy, close-to-close
        rv = strat_r.rolling(vol_window, min_periods=int(vol_window * 0.8)).std() * np.sqrt(252)
        scale = scale * (target_vol / rv).clip(upper=1.0).fillna(1.0)

    scale_m = _hold_monthly(pd.DataFrame({"s": scale}))["s"]      # exposure set at month-end only
    return w.mul(scale_m, axis=0)
