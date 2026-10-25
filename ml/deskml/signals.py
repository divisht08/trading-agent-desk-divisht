"""Quantitative signal layer: indicators -> score (-100..100) -> stance.

The same `score_frame` runs in the live engine and in the backtester, so what you
backtest is exactly the screen the agent uses to pick candidates.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BULL_T = 25.0
BEAR_T = -25.0


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def score_frame(df: pd.DataFrame, bench: pd.Series | None = None) -> pd.DataFrame:
    """Return per-bar indicators plus `score` and `stance`. Uses only data up to each bar."""
    c = df["close"]
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    out["sma20"] = c.rolling(20).mean()
    out["sma50"] = c.rolling(50).mean()
    out["sma200"] = c.rolling(200, min_periods=120).mean()
    out["rsi14"] = rsi(c)
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    out["macd_hist"] = macd - macd.ewm(span=9, adjust=False).mean()
    out["atr14"] = atr(df)
    out["atr_pct"] = out["atr14"] / c * 100
    out["ret20"] = c.pct_change(20) * 100
    out["ret60"] = c.pct_change(60) * 100
    out["hi20"] = df["high"].rolling(20).max().shift()
    out["lo20"] = df["low"].rolling(20).min().shift()
    out["vol_ratio"] = df["volume"] / df["volume"].rolling(20).mean().shift()
    out["turnover_cr"] = (c * df["volume"]).rolling(20).mean() / 1e7  # ₹ crore / day
    if bench is not None and len(bench):
        b = bench.reindex(c.index).ffill()
        out["rs20"] = out["ret20"] - b.pct_change(20) * 100
    else:
        out["rs20"] = 0.0

    s = pd.Series(0.0, index=c.index)
    # Trend (max ±35)
    s += np.where(c > out["sma50"], 12, -12)
    s += np.where(out["sma50"] > out["sma200"], 13, -13)
    s += np.where(c > out["sma20"], 10, -10)
    # Momentum vs market (±20)
    s += (out["rs20"].fillna(0) * 2).clip(-20, 20)
    # MACD (±15)
    s += (np.sign(out["macd_hist"]) * 8 + np.sign(out["macd_hist"].diff()) * 7).fillna(0)
    # RSI regime (±15): strength 55-70 good, >78 stretched, <30 washed out
    r = out["rsi14"]
    s += np.select([r > 78, r >= 55, r <= 30, r < 45], [-8, 15, 5, -15], 0)
    # Breakouts on volume (±15)
    vr = out["vol_ratio"].fillna(1)
    s += np.where((c > out["hi20"]) & (vr > 1.5), 15, 0)
    s += np.where((c < out["lo20"]) & (vr > 1.5), -15, 0)
    out["score"] = s.clip(-100, 100).round(1)
    out["stance"] = np.select([out["score"] >= BULL_T, out["score"] <= BEAR_T], ["bullish", "bearish"], "neutral")
    return out


def reasons(row: pd.Series) -> list[str]:
    """Human-readable explanation of the last bar, for the dashboard and the agents."""
    r: list[str] = []
    c = row["close"]
    if pd.notna(row.get("sma50")):
        r.append(f"{'above' if c > row['sma50'] else 'below'} 50-day average")
    if pd.notna(row.get("sma200")):
        r.append(f"50-day {'above' if row['sma50'] > row['sma200'] else 'below'} 200-day")
    r.append(f"RSI {row['rsi14']:.0f}")
    if pd.notna(row.get("rs20")):
        r.append(f"{row['rs20']:+.1f}% vs Nifty over 20 days")
    if pd.notna(row.get("hi20")) and c > row["hi20"]:
        r.append("new 20-day high" + (" on heavy volume" if row.get("vol_ratio", 1) > 1.5 else ""))
    if pd.notna(row.get("lo20")) and c < row["lo20"]:
        r.append("new 20-day low" + (" on heavy volume" if row.get("vol_ratio", 1) > 1.5 else ""))
    r.append(f"MACD {'rising' if row['macd_hist'] > 0 else 'falling'}")
    return r


def with_live_bar(daily: pd.DataFrame, quote: dict | None) -> pd.DataFrame:
    """Daily history plus today's live price as a provisional bar."""
    df = daily.copy()
    if quote and quote.get("last_price"):
        lp = float(quote["last_price"])
        today = pd.Timestamp.now().normalize()
        o = quote.get("ohlc") or {}
        bar = {"open": o.get("open") or lp, "high": max(o.get("high") or lp, lp),
               "low": min(o.get("low") or lp, lp), "close": lp,
               "volume": quote.get("volume") or df["volume"].iloc[-1]}
        if df.index[-1] >= today:
            df = df.iloc[:-1]
        df.loc[today] = bar
    return df


def live_snapshot(daily: pd.DataFrame, quote: dict | None, bench: pd.Series | None) -> dict | None:
    """Rule score using history plus today's live price. Returns None if history is too short."""
    if daily is None or len(daily) < 60:
        return None
    df = with_live_bar(daily, quote)
    f = score_frame(df, bench)
    last = f.iloc[-1]
    prev_close = float(df["close"].iloc[-2])
    feats = {k: (None if pd.isna(v) else round(float(v), 4)) for k, v in last.items() if k != "stance"}
    return {
        "ltp": float(last["close"]),
        "change_pct": round((float(last["close"]) / prev_close - 1) * 100, 2),
        "score": float(last["score"]),
        "stance": str(last["stance"]),
        "features": feats,
        "reasons": reasons(last),
        "spark": [round(float(x), 2) for x in df["close"].iloc[-30:]],
        "frame": df,
    }


def stance_of(score: float) -> str:
    return "bullish" if score >= BULL_T else "bearish" if score <= BEAR_T else "neutral"
