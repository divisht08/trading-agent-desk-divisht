import sys; sys.path.insert(0,'.'); sys.path.insert(0,'strategies')
import pandas as pd, numpy as np, harness as H, json
import trend_timing as T
p = H.load_panel(); univ = pd.read_pickle('audit/univ_top100.pkl')
f = {s: T.exposure(p, univ, s) for s in ("sma200_monthly", "sma10m", "combo", "sma200_daily")}
a, b = f["sma200_monthly"], f["sma10m"]
print("sma200_monthly vs sma10m identical days:", float((a == b).mean()), "differing days:", int((a != b).sum()))
d = a.index[a != b]
print("differing dates sample:", list(d[:10].date))
idx = T.market_index(p)
me = idx[H.month_end_mask(idx.index).values]
print("month-end flags differ:", int(((me > me.rolling(10, min_periods=10).mean()) != (idx > idx.rolling(200, min_periods=200).mean())[me.index]).sum()), "of", len(me))
# chained index vs Nifty
n = H.load_nifty().reindex(p.dates)
rr = (idx.pct_change() - n.pct_change()).loc["2008":]
print("days chained index return != nifty return (|diff|>1e-9):", int((rr.abs() > 1e-9).sum()))
# exposure switches per year for combo
c = f["combo"]
print("combo switches per year:", (c.diff().abs() > 0).groupby(c.index.year).sum().loc[2008:].to_dict())
print("combo exposure by year:", c.groupby(c.index.year).mean().loc[2008:].round(2).to_dict())
