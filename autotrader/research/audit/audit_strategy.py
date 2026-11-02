"""Independent audit of one strategy. Usage: python3 audit/audit_strategy.py <name>
Writes audit/out_<name>.json. Does not modify any existing file."""
import sys, json, time, importlib
from pathlib import Path
sys.path.insert(0, '.'); sys.path.insert(0, 'strategies')
import numpy as np, pandas as pd
import harness as H

name = sys.argv[1]
OUT = Path('audit') / f'out_{name}.json'
t0 = time.time()
panel = H.load_panel()
ucache = Path('audit/univ_top100.pkl')
if ucache.exists():
    univ = pd.read_pickle(ucache)
else:
    univ = H.liquid_universe(panel); univ.to_pickle(ucache)
mod = importlib.import_module(name)
res = json.loads(Path(f'results/{name}/results.json').read_text())
params = res['chosen_params']
P = H.PERIODS
FULL, IS, OOS = P['full_2008_2026'], P['in_sample_2008_2019'], P['out_of_sample_2020_2026']
out = {'strategy': name, 'params': params}

def m(ret, per, sim=None):
    return H.metrics(ret, *per, **({} if sim is None else {'turnover': sim['turnover'], 'exposure': sim['exposure']}))

def brief(x):
    return {k: x.get(k) for k in ('cagr_pct', 'sharpe', 'max_drawdown_pct', 'annual_turnover_x', 'avg_exposure_pct') if k in x}

# ---- (b) reproduce
W = mod.weights(panel, univ, **params)
out['t_weights_s'] = round(time.time() - t0, 1)
sim = H.simulate(W, panel)
rep = {k: m(sim['ret'], P[k], sim) for k in ('full_2008_2026', 'in_sample_2008_2019', 'out_of_sample_2020_2026')}
out['reproduced'] = {k: brief(v) for k, v in rep.items()}
out['reported'] = {k: brief(res['periods'][k]) for k in rep}
out['repro_max_abs_metric_diff'] = max(abs((rep[k].get(f) or 0) - (res['periods'][k].get(f) or 0))
                                       for k in rep for f in ('cagr_pct', 'sharpe', 'max_drawdown_pct'))
dr = pd.read_csv(f'results/{name}/daily_returns.csv', index_col=0, parse_dates=True)
out['daily_csv_max_abs_ret_diff'] = float((dr['ret'] - sim['ret'].reindex(dr.index)).abs().max())

# ---- (c) stress: extra delay, 2x/3x costs, delay+2x
def run(Wx, cost=H.COST_PER_SIDE):
    s = H.simulate(Wx, panel, cost)
    return {k: brief(m(s['ret'], P[k], s)) for k in ('full_2008_2026', 'out_of_sample_2020_2026')}
Wn = W.reindex(index=panel.dates, columns=panel.close.columns).fillna(0.0)
out['stress'] = {
    'base': {k: out['reproduced'][k] for k in ('full_2008_2026', 'out_of_sample_2020_2026')},
    'delay_1d': run(Wn.shift(1).fillna(0.0)),
    'delay_2d': run(Wn.shift(2).fillna(0.0)),
    'cost_2x': run(Wn, 2 * H.COST_PER_SIDE),
    'cost_3x': run(Wn, 3 * H.COST_PER_SIDE),
    'delay_1d_cost_2x': run(Wn.shift(1).fillna(0.0), 2 * H.COST_PER_SIDE),
}

# ---- (e) cash yield 6%/yr on uninvested share, from the published daily_returns.csv
cy = dr['ret'] + (1 - dr['exposure']) * 0.06 / 252
out['cash_yield_6pct'] = {k: brief(H.metrics(cy, *P[k])) for k in ('full_2008_2026', 'in_sample_2008_2019', 'out_of_sample_2020_2026')}
out['avg_exposure'] = {k: round(float(dr['exposure'].loc[P[k][0]:P[k][1]].mean()), 3) for k in ('full_2008_2026', 'out_of_sample_2020_2026')}

# ---- drift-aware simulation (buy-and-hold shares between trades, costs on real trades)
o = panel.open.astype('float64')
r = (o.shift(-1) / o - 1)
r_raw = r.copy()
r = r.where(r.abs() <= H.MAX_DAILY_MOVE, 0.0).fillna(0.0)
s_ = Wn.clip(lower=0).sum(axis=1)
Wc = Wn.clip(lower=0).div(s_.where(s_ > 1, 1.0), axis=0)
heldT = Wc.shift(1).fillna(0.0)
cols = heldT.columns[(heldT != 0).any().to_numpy()]
HT = heldT[cols].to_numpy(); RR = r[cols].to_numpy()
mode = 'per_name' if name in ('breakout_v2', 'mean_reversion') else 'full'
def drift_sim(cost):
    T, N = HT.shape
    h = np.zeros(N); rets = np.zeros(T); tos = np.zeros(T); prev = np.zeros(N)
    for t in range(T):
        tgt = HT[t]
        ch = np.abs(tgt - prev) > 1e-12
        if ch.any():
            if mode == 'full':
                new = tgt.copy()
            else:
                new = h.copy(); new[ch] = tgt[ch]
                sm = new.sum()
                if sm > 1: new /= sm
            tos[t] = np.abs(new - h).sum(); h = new
        prev = tgt
        g = float(h @ RR[t]); c = tos[t] * cost
        rets[t] = g - c
        h = h * (1 + RR[t]) / (1 + g - c) if (1 + g - c) > 0 else h * 0
    return pd.Series(rets, index=heldT.index), pd.Series(tos, index=heldT.index)
dret, dto = drift_sim(H.COST_PER_SIDE)
out['drift_aware'] = {'mode': mode}
for k in ('full_2008_2026', 'out_of_sample_2020_2026'):
    out['drift_aware'][k] = brief(H.metrics(dret, *P[k], turnover=dto, exposure=sim['exposure']))

# ---- (f) concentration: top contributors (arithmetic sum of daily held*r over full period)
mask = (heldT.index >= FULL[0]) & (heldT.index < FULL[1])
contrib = (heldT[cols] * r[cols])[mask].sum()
tot = float(contrib.sum())
top = contrib.sort_values(ascending=False).head(5)
out['top5_contributors_full'] = {k: round(float(v) * 100, 2) for k, v in top.items()}
out['sum_arith_contrib_pct'] = round(tot * 100, 2)
out['top5_share_of_arith_sum'] = round(float(top.sum()) / tot, 3) if tot else None
out['n_names_held_full'] = int(((heldT[cols][mask] > 0).any()).sum())
# CAGR with top-5 names' returns zeroed (as if that money sat in cash)
r_ex = r.copy(); r_ex[list(top.index)] = 0.0
g_ex = (heldT * r_ex).sum(axis=1) - sim['cost']
out['ex_top5'] = {k: brief(H.metrics(g_ex, *P[k])) for k in ('full_2008_2026', 'out_of_sample_2020_2026')}
# OOS top contributors
mo = (heldT.index >= OOS[0]) & (heldT.index < OOS[1])
co = (heldT[cols] * r[cols])[mo].sum().sort_values(ascending=False).head(5)
out['top5_contributors_oos'] = {k: round(float(v) * 100, 2) for k, v in co.items()}

# ---- data-error rule: held positions hitting |r|>40% (zeroed) and 25-40% (kept)
hr = heldT[cols]; rr = r_raw[cols]
ev = rr.where((hr > 0) & (rr.abs() > H.MAX_DAILY_MOVE)).stack().dropna()
out['zeroed_moves_held'] = [{'date': str(d.date()), 'sym': s, 'raw_r': round(float(v), 3), 'w': round(float(hr.at[d, s]), 4)}
                            for (d, s), v in ev.items() if d >= pd.Timestamp('2008-01-01')]
ev2 = rr.where((hr > 0) & (rr.abs() > 0.25) & (rr.abs() <= H.MAX_DAILY_MOVE)).stack().dropna()
out['moves_25_40_held'] = [{'date': str(d.date()), 'sym': s, 'raw_r': round(float(v), 3), 'w': round(float(hr.at[d, s]), 4)}
                           for (d, s), v in ev2.items() if d >= pd.Timestamp('2008-01-01')]

# ---- stronger look-ahead check: mid-month cuts, compare weights strictly before the cut day
cuts = ('2009-11-18', '2013-02-13', '2017-08-09', '2021-05-19', '2025-12-10')
worst = 0.0; per = {}
for ccut in cuts:
    c_ = pd.Timestamp(ccut)
    p2 = panel.upto(c_); u2 = univ.loc[:c_]
    part = mod.weights(p2, u2, **params).reindex(index=p2.dates, columns=panel.close.columns).fillna(0.0)
    prev_day = p2.dates[p2.dates < c_][-1]
    d = float((Wn.loc[:prev_day] - part.loc[:prev_day]).abs().to_numpy().max())
    d_cut = float((Wn.loc[c_] - part.loc[c_]).abs().max())
    per[ccut] = {'max_diff_before_cut': d, 'diff_on_cut_day': round(d_cut, 6)}
    worst = max(worst, d)
out['midmonth_lookahead'] = {'passed': worst < 1e-9, 'cuts': per}
out['row_sum'] = {'max': round(float(Wn.sum(axis=1).max()), 6), 'min_w': float(Wn.min().min())}
out['elapsed_s'] = round(time.time() - t0, 1)
OUT.write_text(json.dumps(out, indent=1, default=str))
print(json.dumps(out, indent=1, default=str))
