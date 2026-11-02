"""Walk-forward validation + go/no-go gate. Writes validation.json, which the live engine obeys.

Method (fixed BEFORE looking at results, to avoid fitting the rules to the data):
  1. Split history in time: first 70% = in-sample (IS), last 30% = out-of-sample (OOS).
  2. Try a small, pre-declared parameter grid on IS only. Pick the best IS Sharpe.
  3. Run that one parameter set on OOS, untouched. Also report OOS for EVERY grid point:
     if only one corner of the grid works, the edge is probably luck.
  4. A strategy is enabled only if OOS passes all gate rules below (after costs + slippage).

Usage: python -m agent.validate            (expects data/daily and data/5m from fetch_history)
"""
from __future__ import annotations

import itertools
import json
import statistics
from datetime import datetime

import pandas as pd

from . import backtest, backtest_intraday
from .config import Config

SWING_GRID = {"breakout_lookback": [20, 55], "atr_stop_mult": [2.0, 3.0], "volume_mult": [1.0, 1.3]}
ORB_GRID = {"opening_range_minutes": [15, 30], "target_r": [1.5, 2.5], "volume_mult": [1.2, 1.6]}

SWING_GATE = {"min_trades": 30, "min_profit_factor": 1.2, "max_drawdown_pct": -20.0,
              "beat_benchmark_sharpe": True, "min_grid_positive_share": 0.5}
ORB_GATE = {"min_trades": 50, "min_profit_factor": 1.2, "min_avg_r": 0.10,
            "max_drawdown_pct": -10.0, "min_sharpe": 1.0, "min_grid_positive_share": 0.5}


def grid(g: dict) -> list[dict]:
    keys = list(g)
    return [dict(zip(keys, v)) for v in itertools.product(*g.values())]


def split_date(dates: pd.Series, frac: float = 0.7) -> str:
    d = sorted(dates.unique())
    return str(pd.Timestamp(d[int(len(d) * frac)]).date())


def walk_forward(run, data, cfg, g, start_is, split, end) -> dict:
    is_res = []
    for p in grid(g):
        r = run(data, cfg, p, start_is, split)
        is_res.append((r.get("sharpe", 0) or 0, p, r))
    is_res.sort(key=lambda x: x[0], reverse=True)
    best_p = is_res[0][1]
    oos_all = [(p, run(data, cfg, p, split, end)) for _, p, _ in is_res]
    oos_best = next(r for p, r in oos_all if p == best_p)
    pos_share = sum(1 for _, r in oos_all if (r.get("return_pct") or 0) > 0) / len(oos_all)
    return {"best_params": best_p, "in_sample": is_res[0][2], "out_of_sample": oos_best,
            "oos_grid_positive_share": round(pos_share, 2),
            "oos_grid_median_return_pct": statistics.median([r.get("return_pct") or 0 for _, r in oos_all])}


def check(res: dict, gate: dict, bench: dict | None = None) -> tuple[bool, list[str]]:
    o, fails = res["out_of_sample"], []
    if o.get("trades", 0) < gate["min_trades"]:
        fails.append(f"only {o.get('trades', 0)} OOS trades (< {gate['min_trades']})")
    pf = o.get("profit_factor")
    if pf is None or pf < gate["min_profit_factor"]:
        fails.append(f"profit factor {pf} < {gate['min_profit_factor']}")
    if (o.get("max_drawdown_pct") or 0) < gate["max_drawdown_pct"]:
        fails.append(f"drawdown {o.get('max_drawdown_pct')}% worse than {gate['max_drawdown_pct']}%")
    if "min_avg_r" in gate and (o.get("avg_r") or 0) < gate["min_avg_r"]:
        fails.append(f"avg R {o.get('avg_r')} < {gate['min_avg_r']}")
    if "min_sharpe" in gate and (o.get("sharpe") or 0) < gate["min_sharpe"]:
        fails.append(f"Sharpe {o.get('sharpe')} < {gate['min_sharpe']}")
    if gate.get("beat_benchmark_sharpe") and bench and (o.get("sharpe") or 0) < bench["sharpe"]:
        fails.append(f"Sharpe {o.get('sharpe')} below Nifty buy-and-hold {bench['sharpe']}")
    if res["oos_grid_positive_share"] < gate["min_grid_positive_share"]:
        fails.append(f"only {res['oos_grid_positive_share']:.0%} of parameter sets profitable OOS (fragile)")
    return not fails, fails


def main():
    cfg = Config.load()
    out = {"generated": datetime.now().isoformat(timespec="seconds"), "strategies": {}}

    daily = backtest.load_dir("data/daily")
    nifty = daily.pop("NIFTY50", None)
    if daily:
        all_dates = pd.concat([d["date"] for d in daily.values()])
        start, end = str(all_dates.min().date()), str((all_dates.max() + pd.Timedelta(days=1)).date())
        # first 220 bars are EMA warm-up; split on the remainder
        warm = sorted(all_dates.unique())[220]
        split = split_date(all_dates[all_dates >= warm])
        res = walk_forward(backtest.run, daily, cfg, SWING_GRID, start, split, end)
        bench = backtest.buy_hold(nifty, split, end) if nifty is not None else None
        ok, fails = check(res, SWING_GATE, bench)
        out["strategies"]["swing_trend"] = {**res, "benchmark_nifty_oos": bench, "split": split,
                                            "passed": ok, "fail_reasons": fails}

    d5 = backtest_intraday.load_dir("data/5m")
    if d5:
        all_dates = pd.concat([d["date"] for d in d5.values()])
        start, end = str(all_dates.min().date()), str((all_dates.max() + pd.Timedelta(days=1)).date())
        split = split_date(all_dates.dt.normalize())
        res = walk_forward(backtest_intraday.run, d5, cfg, ORB_GRID, start, split, end)
        ok, fails = check(res, ORB_GATE)
        out["strategies"]["intraday_orb"] = {**res, "split": split, "passed": ok, "fail_reasons": fails}

    with open("validation.json", "w") as f:
        json.dump(out, f, indent=2, default=float)
    for name, r in out["strategies"].items():
        print(f"\n== {name}: {'PASS' if r['passed'] else 'FAIL'}  params={r['best_params']}")
        print("   IS :", r["in_sample"])
        print("   OOS:", r["out_of_sample"])
        if r.get("benchmark_nifty_oos"):
            print("   Nifty OOS:", r["benchmark_nifty_oos"])
        for x in r["fail_reasons"]:
            print("   x", x)


if __name__ == "__main__":
    main()
