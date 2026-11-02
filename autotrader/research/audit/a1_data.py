import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd, harness as H
p = H.load_panel()
c=p.close; o=p.open
print("panel", c.shape, c.index[0].date(), c.index[-1].date())
last = c.apply(lambda s: s.last_valid_index())
first = c.apply(lambda s: s.first_valid_index())
print("symbols ending before 2026-01-01:", int((last < "2026-01-01").sum()), "of", len(last))
print("ending before 2015:", int((last < "2015-01-01").sum()))
u = H.liquid_universe(p)
ever = u.any()
print("ever in top100:", int(ever.sum()), " of which ended before 2026:", int((last[ever] < "2026-01-01").sum()))
# universe size by year
print("univ size per year (median):", u.sum(axis=1).groupby(u.index.year).median().to_dict())
# Nifty
n = H.load_nifty()
print("nifty", n.index[0].date(), n.index[-1].date(), len(n))
nn = n.loc["2007-12-01":]
gaps = nn.index.to_series().diff().dt.days
print("max calendar gap days:", gaps.max(), gaps.idxmax())
print("gaps > 5 days:", gaps[gaps>5].to_dict())
r = nn.pct_change()
z = (r==0)
run = z.groupby((~z).cumsum()).cumsum()
print("max consecutive zero-return days:", run.max(), "count zero days:", int(z.sum()))
print("abs daily ret > 8%:", r[r.abs()>0.08].round(3).to_dict())
pd_ = p.dates[(p.dates>="2008-01-01")]
miss = pd_.difference(nn.index)
print("panel dates 2008+ missing in nifty:", len(miss), list(miss[:10].date), list(miss[-5:].date))
extra = nn.index.difference(p.dates)
print("nifty dates not in panel:", len(extra), list(extra[:10].date))
# splice point check
a = pd.read_csv(H.NIFTY_PATH, usecols=["Date","Close"], parse_dates=["Date"]).dropna().set_index("Date")["Close"]
b = pd.read_csv(H.NIFTY_NSE_PATH, usecols=["Date","Close"], parse_dates=["Date"]).dropna().set_index("Date")["Close"]
print("src a", a.index[0].date(), a.index[-1].date(), "src b", b.index[0].date(), b.index[-1].date())
j = a.index.intersection(b.index)
print("overlap days", len(j), "max rel diff", float((a[j]/b[j]-1).abs().max()))
# year returns
yr = nn.resample("YE").last().pct_change()
print("nifty yearly:", (yr*100).round(1).to_dict())
# raw open-to-open extreme moves
r = (o.shift(-1)/o - 1)
big = (r.abs()>0.4)
print("open-open |r|>40% events (all stocks):", int(big.sum().sum()), "  in universe:", int((big & u).sum().sum()))
mid = (r.abs()>0.25)&(r.abs()<=0.4)&u
print("25-40% events in universe:", int(mid.sum().sum()))
# list in-universe >40% events
ev = r.where(big & u).stack()
print(ev.sort_values().head(25).round(3).to_string())
print(ev.sort_values().tail(10).round(3).to_string())
# gaps: in-universe stock with NaN open next day but trading later
nxt_nan = o.shift(-1).isna() & o.notna() & u
later = o.iloc[::-1].notna().cumsum().iloc[::-1].shift(-1).fillna(0)>0
print("in-univ days followed by a non-trading day while stock trades later (gap starts):", int((nxt_nan & later).sum().sum()),
      " permanent stops while in univ:", int((nxt_nan & ~later).sum().sum()))
