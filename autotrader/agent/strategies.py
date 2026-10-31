"""Strategies emit Signals; risk manager sizes and approves them. Long-only (cash equity)."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .indicators import atr, ema, vwap


@dataclass
class Signal:
    symbol: str
    strategy: str
    product: str          # MIS (intraday) | CNC (delivery/swing)
    entry: float
    stop: float
    target: float | None
    reason: str


def intraday_orb(symbol: str, bars5: pd.DataFrame, p: dict, now_ts: pd.Timestamp) -> Signal | None:
    """Opening-range breakout, long only. `bars5` must contain COMPLETED bars only.

    Logic: the first N minutes set the day's balance area. A first close above that range,
    on expanded volume and above session VWAP, signals buyers taking control. Entry is the
    next bar's open; stop is below the range (capped at atr_stop_mult x 5-min ATR); target
    is target_r x risk. Skip chasing: no entry if price is already > 1.5 ATR past the range.
    """
    if bars5.empty:
        return None
    day = bars5[bars5["date"].dt.date == now_ts.date()].reset_index(drop=True)
    n_or = max(1, p["opening_range_minutes"] // 5)
    if len(day) <= n_or + 1:
        return None
    rng = day.iloc[:n_or]
    or_high, or_low = rng["high"].max(), rng["low"].min()
    last, prev = day.iloc[-1], day.iloc[-2]
    prev_vol = bars5["volume"].iloc[-21:-1].mean()
    a = atr(bars5).iloc[-1]
    vw = vwap(day).iloc[-1]
    if pd.isna(a) or a <= 0 or prev_vol <= 0:
        return None
    fresh = prev["close"] <= or_high < last["close"]          # first close above the range
    if not fresh or last["close"] > or_high + 1.5 * a:          # don't chase
        return None
    if last["close"] > vw and last["volume"] >= p["volume_mult"] * prev_vol:
        entry = float(last["close"])
        stop = max(float(or_low), entry - p["atr_stop_mult"] * a)
        stop = min(stop, entry - 0.5 * a)                       # minimum breathing room
        return Signal(symbol, "intraday_orb", "MIS", entry, stop,
                      entry + p["target_r"] * (entry - stop),
                      f"ORB>{or_high:.2f} vol x{last['volume']/prev_vol:.1f} >VWAP")
    return None


def swing_trend(symbol: str, daily: pd.DataFrame, p: dict) -> Signal | None:
    """Trend-following breakout on daily bars: close above N-day high, in an uptrend
    (fast EMA > slow EMA, price > fast EMA), on above-average volume."""
    if len(daily) < p["ema_slow"] + 5:
        return None
    c = daily["close"]
    f, s = ema(c, p["ema_fast"]), ema(c, p["ema_slow"])
    hh = daily["high"].iloc[-p["breakout_lookback"] - 1:-1].max()
    vavg = daily["volume"].iloc[-21:-1].mean()
    a = atr(daily).iloc[-1]
    last = daily.iloc[-1]
    if (last["close"] > hh and f.iloc[-1] > s.iloc[-1] and last["close"] > f.iloc[-1]
            and last["volume"] >= p["volume_mult"] * vavg
            and last["close"] < f.iloc[-1] * 1.15):           # not wildly extended
        entry = float(last["close"])
        stop = entry - p["atr_stop_mult"] * a
        return Signal(symbol, "swing_trend", "CNC", entry, stop, None,
                      f"breakout >{hh:.2f}, EMA{p['ema_fast']}>EMA{p['ema_slow']}")
    return None


def swing_exit(pos: dict, daily: pd.DataFrame, price: float, p: dict, now: pd.Timestamp) -> tuple[bool, float, str]:
    """Returns (exit?, new_trailing_stop, reason). Trails stop under 2.5xATR from peak;
    exits on close under fast-EMA or max hold."""
    a = atr(daily).iloc[-1]
    peak = max(pos.get("peak") or pos["entry"], price)
    trail = max(pos["stop"], peak - p["atr_stop_mult"] * a)
    if price <= trail:
        return True, trail, "trailing stop"
    f = ema(daily["close"], p["ema_fast"]).iloc[-1]
    if daily["close"].iloc[-1] < f:
        return True, trail, f"close below EMA{p['ema_fast']}"
    held = (now - pd.Timestamp(pos["opened_at"])).days
    if held >= p["max_hold_days"]:
        return True, trail, "max hold"
    return False, trail, ""
