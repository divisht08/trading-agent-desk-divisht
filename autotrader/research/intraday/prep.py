"""Resample NSE 1-minute CSVs (untrusted data, read as plain CSV only) to 5-minute bars and validate.

Usage: python3 -I prep.py <minute_dir> <out_dir> <quality_json>
Bars are labelled by their START time (09:15 bar = 09:15:00-09:19:59), regular session 09:15-15:30 only.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SYMS = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "AXISBANK", "KOTAKBANK", "LT", "ITC",
        "BHARTIARTL", "MARUTI", "BAJFINANCE", "HDFC", "SUNPHARMA", "ASIANPAINT"]


def main(src: str, out: str, qpath: str) -> None:
    Path(out).mkdir(parents=True, exist_ok=True)
    q: dict = {}
    days_by_sym = {}
    for s in SYMS:
        f = Path(src) / f"{s}__EQ__NSE__NSE__MINUTE.csv"
        d = pd.read_csv(f, dtype={"open": float, "high": float, "low": float, "close": float, "volume": float})
        ts = pd.to_datetime(d.pop("timestamp"), utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        d.index = ts
        info = {"rows_1m": len(d), "first": str(ts.min()), "last": str(ts.max()),
                "dup_timestamps": int(ts.duplicated().sum()),
                "nonpositive_price": int((d[["open", "high", "low", "close"]] <= 0).any(axis=1).sum()),
                "high_lt_low": int((d.high < d.low).sum()),
                "ohlc_inconsistent": int(((d.high < d[["open", "close"]].max(axis=1)) |
                                          (d.low > d[["open", "close"]].min(axis=1))).sum()),
                "nan_rows": int(d.isna().any(axis=1).sum()), "zero_volume_1m": int((d.volume == 0).sum())}
        d = d[~d.index.duplicated(keep="first")].sort_index()
        t = d.index.time
        in_sess = (t >= pd.Timestamp("09:15").time()) & (t < pd.Timestamp("15:30").time())
        info["rows_outside_session_dropped"] = int((~in_sess).sum())
        d = d[in_sess & (d[["open", "high", "low", "close"]] > 0).all(axis=1)]
        mins_per_day = d.groupby(d.index.date).size()
        # special sessions (e.g. Muhurat) and badly truncated days
        short = mins_per_day[mins_per_day < 300]
        info["days_total"] = int(len(mins_per_day))
        info["short_days_lt300min_dropped"] = {str(k): int(v) for k, v in short.items()}
        d = d[~pd.Index(d.index.date).isin(short.index)]
        info["median_minutes_per_day"] = float(mins_per_day.median())
        # largest 1-minute close-to-close jumps within a day (possible bad ticks)
        r = d.close.groupby(d.index.date).pct_change().abs()
        info["max_intraday_1m_jump_pct"] = round(float(r.max() * 100), 2)
        info["n_1m_jumps_gt5pct"] = int((r > 0.05).sum())
        g = d.resample("5min", label="left", closed="left")
        b = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(),
                          "close": g.close.last(), "volume": g.volume.sum()}).dropna(subset=["open"])
        b.index.name = "date"
        b = b.reset_index()
        bars_per_day = b.groupby(b.date.dt.date).size()
        info["days_kept"] = int(len(bars_per_day))
        info["bars_5m"] = int(len(b))
        info["days_with_lt75_bars"] = int((bars_per_day < 75).sum())
        # overnight gaps > 15% (would flag unadjusted splits/bonuses)
        dc = b.groupby(b.date.dt.date).agg(o=("open", "first"), c=("close", "last"))
        gap = (dc.o / dc.c.shift() - 1).abs()
        info["overnight_gaps_gt15pct"] = {str(k): round(float(v) * 100, 1) for k, v in gap[gap > 0.15].items()}
        days_by_sym[s] = set(bars_per_day.index)
        b.to_csv(Path(out) / f"{s}.csv", index=False)
        q[s] = info
        print(s, info["bars_5m"], info["days_kept"], info["first"], info["last"], flush=True)
    alldays = sorted(set().union(*days_by_sym.values()))
    q["_union_trading_days"] = len(alldays)
    q["_missing_days_vs_union"] = {s: sorted(str(x) for x in set(alldays) - v) for s, v in days_by_sym.items()}
    by_year = pd.Series(1, index=pd.to_datetime(alldays)).groupby(lambda x: x.year).sum()
    q["_union_days_per_year"] = {int(k): int(v) for k, v in by_year.items()}
    Path(qpath).write_text(json.dumps(q, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:4])
