import sys; sys.path.insert(0,'.'); sys.path.insert(0,'strategies')
import pandas as pd, numpy as np, harness as H, json
p = H.load_panel(); univ = pd.read_pickle('audit/univ_top100.pkl')
o = p.open.astype(float)
# (1) timing unit test: weight 1.0 on one stock decided at close of a single date t
s = 'RELIANCE'; t = p.dates.get_loc(pd.Timestamp('2015-06-10'))
W = pd.DataFrame(0.0, index=p.dates, columns=[s]); W.iloc[t, 0] = 1.0
sim = H.simulate(W, p)
d = p.dates
nz = sim[sim['gross'] != 0]
print("weight decided", d[t].date(), "-> gross accrues on", list(nz.index.date))
print("gross =", float(nz['gross'].iloc[0]), " open(t+2)/open(t+1)-1 =", float(o[s].iloc[t+2]/o[s].iloc[t+1]-1))
print("costs charged on:", list(sim.index[sim['cost'] > 0].date), "turnover", sim['turnover'][sim['turnover']>0].tolist())
# (4) universe point-in-time: recompute on truncated panel and compare
for cut in ('2012-06-29', '2018-03-28', '2023-09-29'):
    u2 = H.liquid_universe(p.upto(cut))
    diff = (u2 != univ.loc[:cut, u2.columns]).to_numpy().sum()
    print("liquid_universe truncated at", cut, "differences:", int(diff))
# open data quality in universe
hi, lo = p.high, p.low
bad = ((o > hi * 1.001) | (o < lo * 0.999)) & univ
print("in-universe days with open outside [low,high]:", int(bad.sum().sum()), "of", int(univ.sum().sum()))
print("in-universe open NaN while close present:", int((o.isna() & p.close.notna() & univ).sum().sum()))
# turnover understatement: share of drift (computed in per-strategy drift sim)
# delisted coverage from eod2 map
m = json.load(open('/home/claude/data_src/eod2/isin_symbol_map.json'))
ended = [h[-1] for h in m['isin2hist'].values() if h and h[-1]['to_date'] < '2025-10-01']
print("ISINs in eod2 history map whose last symbol ended before 2025-10:", len(ended), "; panel symbols:", p.close.shape[1])
# momentum delay sensitivity by year
import momentum_plus as M
Wm = M.weights(p, univ, top_n=10, lookback=252, regime='none').reindex(index=p.dates, columns=p.close.columns).fillna(0)
a = H.simulate(Wm, p)['ret']; b = H.simulate(Wm.shift(1).fillna(0), p)['ret']
ya = (1+a).groupby(a.index.year).prod()-1; yb = (1+b).groupby(b.index.year).prod()-1
print("momentum yearly base minus 1d-delay (pp):", ((ya-yb)*100).loc[2008:].round(1).to_dict())
