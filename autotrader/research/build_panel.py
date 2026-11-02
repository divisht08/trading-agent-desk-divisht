"""Build an aligned daily panel of all NSE equities (ISIN starting INE) from eod2 CSVs.
Output: data/panel.npz with dates, symbols, open, high, low, close, volume (float32, NaN when not trading)."""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd

src = Path(sys.argv[1]); out = Path(sys.argv[2])
m = json.loads((src.parent / "isin_symbol_map.json").read_text())
sym2isin = m["sym2isin"]
eq = sorted(s for s, i in sym2isin.items() if str(i).startswith("INE"))
frames = {}
for s in eq:
    f = src / f"{s.lower()}.csv"
    if not f.exists():
        continue
    try:
        d = pd.read_csv(f, usecols=["Date", "Open", "High", "Low", "Close", "Volume"])
    except Exception:
        continue
    d["Date"] = pd.to_datetime(d["Date"], errors="coerce")
    d = d.dropna(subset=["Date", "Close"]).drop_duplicates("Date").set_index("Date").sort_index()
    d = d[d.index >= "2004-01-01"]
    if len(d) > 60:
        frames[s] = d.astype("float32")
dates = sorted(set().union(*[f.index for f in frames.values()]))
dates = pd.DatetimeIndex(dates)
syms = sorted(frames)
arr = {k: np.full((len(dates), len(syms)), np.nan, dtype="float32") for k in ["Open", "High", "Low", "Close", "Volume"]}
pos = pd.Series(range(len(dates)), index=dates)
for j, s in enumerate(syms):
    f = frames[s]; rows = pos[f.index].values
    for k in arr:
        arr[k][rows, j] = f[k].values
np.savez_compressed(out, dates=dates.values.astype("datetime64[D]"), symbols=np.array(syms),
                    **{k.lower(): v for k, v in arr.items()})
print(len(dates), "dates", dates[0].date(), "to", dates[-1].date(), "|", len(syms), "equities")
