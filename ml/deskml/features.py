"""Feature engineering and labels for the ML model.

Every feature at bar t uses only data up to and including bar t. Labels look
forward HORIZON bars, so training must leave a gap of HORIZON bars before any
evaluation or prediction date (see model.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .signals import score_frame

HORIZON = 5            # trading days ahead
ROUND_TRIP_COST = 0.0025   # ~0.25% delivery round trip (STT both sides + stamp + DP + fees)

FEATURES = [
    "ret1", "ret5", "ret20", "ret60", "rs20", "rs60",
    "dist_sma20", "dist_sma50", "dist_sma200", "sma50_over_200",
    "rsi14", "macd_hist_pct", "atr_pct", "vol20", "vol_ratio", "vol_ratio5",
    "pos_in_52w", "dist_hi20", "dist_lo20", "gap", "range_pct", "rule_score",
    "bench_ret5", "bench_ret20", "bench_vol20",
]


def build_features(df: pd.DataFrame, bench: pd.Series | None = None) -> pd.DataFrame:
    """Feature matrix indexed like df. NaNs remain where history is too short."""
    c = df["close"]
    f = score_frame(df, bench)
    out = pd.DataFrame(index=df.index)
    r = c.pct_change()
    out["ret1"] = r
    out["ret5"] = c.pct_change(5)
    out["ret20"] = c.pct_change(20)
    out["ret60"] = c.pct_change(60)
    if bench is not None and len(bench):
        b = bench.reindex(df.index).ffill()
        br = b.pct_change()
        out["rs20"] = out["ret20"] - b.pct_change(20)
        out["rs60"] = out["ret60"] - b.pct_change(60)
        out["bench_ret5"] = b.pct_change(5)
        out["bench_ret20"] = b.pct_change(20)
        out["bench_vol20"] = br.rolling(20).std()
    else:
        for k in ("rs20", "rs60", "bench_ret5", "bench_ret20", "bench_vol20"):
            out[k] = 0.0
    out["dist_sma20"] = c / f["sma20"] - 1
    out["dist_sma50"] = c / f["sma50"] - 1
    out["dist_sma200"] = c / f["sma200"] - 1
    out["sma50_over_200"] = f["sma50"] / f["sma200"] - 1
    out["rsi14"] = f["rsi14"] / 100
    out["macd_hist_pct"] = f["macd_hist"] / c
    out["atr_pct"] = f["atr_pct"] / 100
    out["vol20"] = r.rolling(20).std()
    out["vol_ratio"] = f["vol_ratio"].clip(0, 10)
    out["vol_ratio5"] = (df["volume"].rolling(5).mean() / df["volume"].rolling(60).mean()).clip(0, 10)
    hi252 = df["high"].rolling(252, min_periods=120).max()
    lo252 = df["low"].rolling(252, min_periods=120).min()
    out["pos_in_52w"] = (c - lo252) / (hi252 - lo252).replace(0, np.nan)
    out["dist_hi20"] = c / f["hi20"] - 1
    out["dist_lo20"] = c / f["lo20"] - 1
    out["gap"] = df["open"] / c.shift() - 1
    out["range_pct"] = (df["high"] - df["low"]) / c
    out["rule_score"] = f["score"] / 100
    return out[FEATURES].replace([np.inf, -np.inf], np.nan)


def forward_return(df: pd.DataFrame, horizon: int = HORIZON) -> pd.Series:
    """Return from the next bar's open to the close `horizon` bars later (how a trade would fill)."""
    entry = df["open"].shift(-1)
    exit_ = df["close"].shift(-horizon)
    return exit_ / entry - 1


def label(df: pd.DataFrame, horizon: int = HORIZON, cost: float = ROUND_TRIP_COST) -> pd.Series:
    """1 if the forward return beats round-trip costs, else 0. NaN where the future is unknown."""
    fr = forward_return(df, horizon)
    y = (fr > cost).astype(float)
    y[fr.isna()] = np.nan
    return y


def panel(data: dict[str, pd.DataFrame], bench: pd.Series | None, with_labels: bool = True) -> pd.DataFrame:
    """Stack features (and labels) for many symbols into one long frame."""
    parts = []
    for sym, df in data.items():
        if df is None or len(df) < 130:
            continue
        x = build_features(df, bench)
        x["symbol"] = sym
        if with_labels:
            x["y"] = label(df)
            x["fwd"] = forward_return(df)
        parts.append(x)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts)
    out.index.name = "date"
    return out.reset_index()
