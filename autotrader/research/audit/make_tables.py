import json
S=['low_vol','momentum_plus','breakout_v2','trend_timing','mean_reversion']
F,O='full_2008_2026','out_of_sample_2020_2026'
def f(x): return f"{x.get('cagr_pct')} / {x.get('sharpe')} / {x.get('max_drawdown_pct')}"
for s in S:
    d=json.load(open(f'audit/out_{s}.json'))
    print(f"\n#### {s} table\n| Variant | Full 2008-26 CAGR / Sharpe / MaxDD | OOS 2020-26 CAGR / Sharpe / MaxDD |\n|---|---|---|")
    print(f"| Reported | {f(d['reported'][F])} | {f(d['reported'][O])} |")
    print(f"| Reproduced | {f(d['reproduced'][F])} | {f(d['reproduced'][O])} |")
    for k,lab in [('delay_1d','+1 day delay'),('delay_2d','+2 day delay'),('cost_2x','2x costs (0.28%/side)'),('cost_3x','3x costs (0.42%/side)'),('delay_1d_cost_2x','+1 day delay and 2x costs')]:
        print(f"| {lab} | {f(d['stress'][k][F])} | {f(d['stress'][k][O])} |")
    print(f"| Drift-aware sim ({d['drift_aware']['mode']}) | {f(d['drift_aware'][F])} | {f(d['drift_aware'][O])} |")
    print(f"| Cash earns 6%/yr | {f(d['cash_yield_6pct'][F])} | {f(d['cash_yield_6pct'][O])} |")
    print(f"| Top-5 contributors zeroed | {f(d['ex_top5'][F])} | {f(d['ex_top5'][O])} |")
    print("top5 full", d['top5_contributors_full'], "share", d['top5_share_of_arith_sum'], "names", d['n_names_held_full'])
    print("top5 oos", d['top5_contributors_oos'])
    print("exposure", d['avg_exposure'], "turnover", d['reproduced'][F].get('annual_turnover_x'))
    print("zeroed", [(e['date'],e['sym'],e['raw_r'],e['w']) for e in d['zeroed_moves_held']])
    print("n 25-40", len(d['moves_25_40_held']), "midmonth", d['midmonth_lookahead']['passed'], max(v['max_diff_before_cut'] for v in d['midmonth_lookahead']['cuts'].values()))
