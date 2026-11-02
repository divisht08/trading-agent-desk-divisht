"""Template strategy: monthly top-N momentum with optional breadth regime filter."""
import numpy as np
import pandas as pd


def weights(panel, univ, top_n=5, lookback=126, skip=21, regime=True, regime_threshold=0.5):
    c = panel.close
    mom = c.shift(skip) / c.shift(lookback) - 1                       # uses data up to t only
    mom = mom.where(univ & (mom > 0))
    ema200 = c.ewm(span=200, adjust=False, min_periods=200).mean()
    above = (c > ema200).where(univ)
    breadth = above.sum(axis=1) / univ.sum(axis=1).replace(0, np.nan)
    rank = mom.rank(axis=1, ascending=False, method="first")
    target = (rank <= top_n).astype(float)
    target = target.div(target.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    if regime:
        target = target.mul((breadth >= regime_threshold).astype(float), axis=0)
    from harness import hold_from_month_end
    return hold_from_month_end(target)                                  # rebalance monthly
