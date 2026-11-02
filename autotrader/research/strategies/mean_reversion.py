"""Short-term mean reversion (Connors-style RSI(2) pullbacks in an uptrend), long only.

Logic (all decided at the close of day t, traded by the harness at the next open):
  * Candidates: stocks in the point-in-time top-100 liquid universe whose close is above their 200-day
    simple moving average (long-term uptrend) and whose 2-day RSI (Wilder smoothing) is below `rsi_entry`
    (short-term oversold).
  * Entries fill free slots (max `max_pos` positions), most oversold (lowest RSI(2)) first. Each position is
    a fixed slot of 1/max_pos of capital; unused slots are cash.
  * Exits: close above its 5-day SMA (the bounce), or `max_hold` trading days held, or the stock leaves the
    universe / stops trading. A stock exited today is not re-entered on the same day.
  * Optional regime filter (`regime=True`): no NEW entries when fewer than 50% of the universe closes above
    its 200-day EMA (same breadth measure as the template). Existing positions still exit by the rules.

Implementation: a single loop over dates with numpy vectors across symbols (position state is
path dependent, so a pure cross-sectional vectorisation is not possible). Only data up to t is used.

Evidence:
  * Jegadeesh (1990, JF) and Lehmann (1990, QJE): 1-week / 1-month returns reverse in US stocks.
  * Connors & Alvarez, "Short Term Trading Strategies That Work" (2009): buy RSI(2) < 5-10 when close >
    200-day MA, exit on close above the 5-day MA. Practitioner evidence, mostly US large caps, pre-2009.
  * India: evidence is mixed - studies of overreaction / reversal on NSE/BSE (e.g. "Mean reversals and stock
    market overreactions: further evidence from India", Afro-Asian J. of Finance and Accounting, 2023;
    intraday prior-return studies in Int. J. of Financial Management) report reversal at short horizons
    but magnitudes small relative to Indian round-trip costs. Known risk: high turnover kills it.

Caveats: closes are not dividend adjusted (an ex-dividend drop can look like a pullback); fixed slot
weights mean intra-holding drift is implicitly rebalanced at zero cost by the harness.

PARAMETER GRID (declared before running, 8 combinations):
    rsi_entry in {5, 10}  x  max_pos in {5, 10}  x  regime in {False, True};  max_hold = 10, exit_ma = 5
"""
import numpy as np
import pandas as pd

GRID = [
    {"rsi_entry": r, "max_pos": m, "regime": g, "max_hold": 10, "exit_ma": 5}
    for r in (5, 10) for m in (5, 10) for g in (False, True)
]


def rsi(close: pd.DataFrame, n: int = 2) -> pd.DataFrame:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(dn > 0, 100.0).where(close.notna())


def weights(panel, univ, rsi_entry=10, max_pos=10, regime=False, max_hold=10, exit_ma=5,
            regime_threshold=0.5):
    c = panel.close
    univ = univ.reindex(index=c.index, columns=c.columns).fillna(False).astype(bool)
    r2 = rsi(c, 2)
    sma200 = c.rolling(200, min_periods=200).mean()
    sma_exit = c.rolling(exit_ma, min_periods=exit_ma).mean()
    entry = univ & (c > sma200) & (r2 < rsi_entry)
    if regime:
        ema200 = c.ewm(span=200, adjust=False, min_periods=200).mean()
        above = (c > ema200).where(univ)
        breadth = above.sum(axis=1) / univ.sum(axis=1).replace(0, np.nan)
        entry = entry & (breadth >= regime_threshold).to_numpy()[:, None]

    E = entry.to_numpy()
    R = np.where(E, r2.to_numpy(), np.inf)
    X = (c > sma_exit).to_numpy()                       # bounce: close above short MA
    U = (univ & c.notna()).to_numpy()
    T, N = E.shape
    held = np.zeros(N, dtype=bool)
    age = np.zeros(N, dtype=np.int32)
    W = np.zeros((T, N))
    slot = 1.0 / max_pos
    for t in range(T):
        # exits on today's close (age counts days held after entry)
        age[held] += 1
        exit_now = held & (X[t] | (age >= max_hold) | ~U[t])
        held &= ~exit_now
        age[~held] = 0
        free = max_pos - int(held.sum())
        if free > 0:
            cand = E[t] & ~held & ~exit_now
            k = int(cand.sum())
            if k:
                idx = np.flatnonzero(cand)
                if k > free:
                    order = np.lexsort((idx, R[t, idx]))      # lowest RSI first, ties by column
                    idx = idx[order[:free]]
                held[idx] = True
                age[idx] = 0
        W[t, held] = slot
    return pd.DataFrame(W, index=c.index, columns=c.columns)
