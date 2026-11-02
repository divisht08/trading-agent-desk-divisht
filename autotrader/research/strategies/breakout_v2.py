"""breakout_v2: Turtle/Donchian-style trend-following breakout, redesigned.

Why a redesign: the live swing breakout (20-day high in an EMA50>EMA200 uptrend, 2.5x ATR stop, trailing
exit on close < EMA50) lost ~2%/yr 2008-2026. Short 20-day breakouts with tight stops get whipsawed in
choppy Indian markets: many small losses, costs on every round trip, and winners cut early.

Logic (daily state machine, decided at the close of t, traded by the harness at the open of t+1):
  Entry  (stock in the point-in-time top-100 liquid universe, not already held):
    - close_t is the highest close of the last `entry_window` days (Donchian breakout; Turtle System 2
      used 55 days, Clenow-style / George & Hwang use the 52-week high), and
    - close_t > 200-day EMA (long-term uptrend; Faber 2007 timing rule), and
    - market regime on: >= 50% of the universe above its 200-day EMA (breadth filter), and
    - a slot is free (max_positions); if more candidates than slots, take the strongest 126-day return.
  Size: volatility parity (Turtle "N" / Clenow 2015 ATR sizing): weight = risk_per_pos * close / ATR20,
    capped at max_weight, and limited to the cash left. The weight is fixed for the life of the trade
    (no daily rebalancing, so no churn costs).
  Exit (any of):
    - close_t < lowest low of the previous `exit_window` days (Donchian exit; Turtle 20/55-day low),
    - stock has no price (stopped trading),
    - regime_mode == "all" and the breadth regime is off (go to cash), else regime only blocks entries.

Evidence: Donchian/Turtle channel breakouts (Dennis & Eckhardt, documented by Faith 2007 "Way of the
Turtle"); Faber 2007 "A Quantitative Approach to Tactical Asset Allocation" (10-month / 200-day trend);
George & Hwang 2004 "The 52-Week High and Momentum Investing" (J. Finance) - stocks near 52-week highs
outperform; Clenow 2015 "Stocks on the Move" (ATR risk-parity sizing, index regime filter); Moskowitz,
Ooi & Pedersen 2012 "Time Series Momentum".

Parameter grid (declared before any results were seen; 3 x 2 x 2 = 12 combinations):
    entry_window in {55, 126, 252}, exit_window in {20, 50}, regime_mode in {"entries", "all"}
Fixed (not tuned): max_positions=20, risk_per_pos=0.001 (10 bp daily ATR risk per position),
max_weight=0.10, trend EMA 200, breadth threshold 0.5, ranking by 126-day return, ATR 20.
Universe: harness default point-in-time top-100 liquid stocks.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

GRID = [
    {"entry_window": e, "exit_window": x, "regime_mode": m}
    for e in (55, 126, 252) for x in (20, 50) for m in ("entries", "all")
]


def weights(panel, univ, entry_window=126, exit_window=50, regime_mode="entries", max_positions=20,
            risk_per_pos=0.001, max_weight=0.10, breadth_threshold=0.5, rank_lookback=126):
    univ = univ.reindex(index=panel.dates, columns=panel.close.columns).fillna(False).astype(bool)
    cols = univ.columns[univ.any(axis=0).to_numpy()]
    u = univ[cols]
    c, h, lo = panel.close[cols], panel.high[cols], panel.low[cols]

    # ---- indicators, all using data up to t only ----
    hi_n = c.rolling(entry_window, min_periods=entry_window).max()
    breakout = c >= hi_n                                                      # new N-day closing high
    ema200 = c.ewm(span=200, adjust=False, min_periods=200).mean()
    above = c > ema200
    breadth = above.where(u).sum(axis=1) / u.sum(axis=1).replace(0, np.nan)
    regime_on = (breadth >= breadth_threshold).to_numpy()
    prev_close = c.shift(1)
    tr = np.fmax(np.fmax((h - lo).to_numpy(), (h - prev_close).abs().to_numpy()),
                 (lo - prev_close).abs().to_numpy())
    tr = pd.DataFrame(tr, index=c.index, columns=cols)
    atr = tr.rolling(20, min_periods=15).mean()
    exit_level = lo.rolling(exit_window, min_periods=exit_window).min().shift(1)  # prior N-day low
    mom = c / c.shift(rank_lookback) - 1

    entry_sig = (breakout & above & u & atr.notna()).to_numpy()
    exit_sig = ((c < exit_level) | c.isna()).to_numpy()
    size = (risk_per_pos * c / atr).clip(upper=max_weight).to_numpy()
    momv = mom.to_numpy()

    # ---- state machine over dates, vectorised across symbols ----
    T, N = entry_sig.shape
    out = np.zeros((T, N))
    w = np.zeros(N)
    for t in range(T):
        held = w > 0
        if held.any():
            kill = held & exit_sig[t]
            if regime_mode == "all" and not regime_on[t]:
                kill = held
            w[kill] = 0.0
        if regime_on[t]:
            cand = entry_sig[t] & (w == 0) & np.isfinite(size[t])
            slots = max_positions - int((w > 0).sum())
            if slots > 0 and cand.any():
                idx = np.flatnonzero(cand)
                score = np.nan_to_num(momv[t, idx], nan=-np.inf)
                idx = idx[np.argsort(-score, kind="stable")][:slots]
                cash = 1.0 - w.sum()
                for j in idx:
                    if cash <= 0.005:
                        break
                    wj = min(size[t, j], cash)
                    w[j] = wj
                    cash -= wj
        out[t] = w
    return pd.DataFrame(out, index=c.index, columns=cols)
