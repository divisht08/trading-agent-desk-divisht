"""ORB study. Usage: python3 -I run_orb.py <bars5_dir> <out_dir>

PRE-DECLARED (before any full-period result was seen):
  grid: opening_range_minutes {15,30} x target_r {1.5,2.5} x volume_mult {1.2,1.6}  (8 combos);
        other params (atr_stop_mult 1.2) and all risk/cost settings from config.yaml unchanged.
  in-sample 2017-01-01..2018-12-31, out-of-sample 2019-01-01..2020-12-31.
  selection: highest in-sample Sharpe (daily P&L / start-of-day equity, all session days incl. no-trade days).
  sensitivity (chosen params only, reported, never used for selection): statutory_pct 0.06 -> 0.023
        (closer to actual Zerodha intraday equity charges) and a zero-cost run.
"""
import sys
sys.path.insert(0, "/home/claude/research/intraday")
import copy
import itertools
import json
import time
from pathlib import Path

import orb_lib as L

IS, OOS = ("2017-01-01", "2019-01-01"), ("2019-01-01", "2021-01-01")
GRID = [dict(opening_range_minutes=a, target_r=b, volume_mult=c)
        for a, b, c in itertools.product([15, 30], [1.5, 2.5], [1.2, 1.6])]
PERIODS = {"2017": ("2017-01-01", "2018-01-01"), "2018": ("2018-01-01", "2019-01-01"),
           "2019": ("2019-01-01", "2020-01-01"), "2020": ("2020-01-01", "2021-01-01"),
           "bad_2018_full_year": ("2018-01-01", "2019-01-01"),
           "bad_2020_feb_mar": ("2020-02-01", "2020-04-01")}


def clean(o):
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    if hasattr(o, "item"):
        return o.item()
    return o


def main(bars: str, out: str) -> None:
    Path(out).mkdir(parents=True, exist_ok=True)
    data = L.load_bars(bars)
    cfg = L.Config.load(L.CFG_PATH)
    days = L.session_days(data)
    res = {"symbols": sorted(data), "grid": GRID, "in_sample": IS, "out_of_sample": OOS,
           "costs_config": cfg["costs"], "risk_config": cfg["risk"], "market_config": cfg["market"],
           "base_params": cfg["strategies"]["intraday_orb"], "grid_results": []}
    trades_store = {}
    for i, p in enumerate(GRID):
        t0 = time.time()
        summ, tr = L.run_capture(data, cfg, p, *IS)
        st = L.stats(tr, cfg, days, *IS)
        res["grid_results"].append({"params": p, "is": st, "is_run_summary": summ})
        print(i, p, "IS sharpe", st["sharpe"], "ret", st["total_return_pct"], f"{time.time()-t0:.0f}s", flush=True)
        trades_store[("is", i)] = tr
    best = max(range(len(GRID)), key=lambda i: res["grid_results"][i]["is"]["sharpe"])
    res["chosen_index"], res["chosen_params"] = best, GRID[best]
    # OOS for every combo (robustness only; selection already fixed above)
    for i, p in enumerate(GRID):
        summ, tr = L.run_capture(data, cfg, p, *OOS)
        res["grid_results"][i]["oos"] = L.stats(tr, cfg, days, *OOS)
        res["grid_results"][i]["oos_run_summary"] = summ
        trades_store[("oos", i)] = tr
        print(i, "OOS sharpe", res["grid_results"][i]["oos"]["sharpe"], flush=True)
    tis, toos = trades_store[("is", best)], trades_store[("oos", best)]
    ch = {"is": res["grid_results"][best]["is"], "oos": res["grid_results"][best]["oos"]}
    # per-year and bad periods: equity compounds within the IS/OOS run; each sub-period stats start from capital
    for name, (a, b) in PERIODS.items():
        tr = tis if a < IS[1] else toos
        ch[name] = L.stats(tr, cfg, days, a, b)
    res["chosen"] = ch
    res["nifty"] = {k: L.nifty_stats(a, b) for k, (a, b) in {"is": IS, "oos": OOS, **PERIODS}.items()}
    # cost sensitivity for the chosen params
    sens = {}
    for label, upd in [("statutory_0.023", {"statutory_pct": 0.023}),
                       ("zero_costs", {"statutory_pct": 0.0, "brokerage_pct": 0.0, "slippage_pct": 0.0})]:
        c2 = L.Config(copy.deepcopy(cfg.raw))
        c2.raw["costs"].update(upd)
        sens[label] = {}
        for per, rng in (("is", IS), ("oos", OOS)):
            _, tr = L.run_capture(data, c2, GRID[best], *rng)
            sens[label][per] = L.stats(tr, c2, days, *rng)
        print(label, sens[label]["is"]["sharpe"], sens[label]["oos"]["sharpe"], flush=True)
    res["cost_sensitivity_chosen"] = sens
    toos.to_csv(Path(out) / "trades_oos_chosen.csv", index=False)
    tis.to_csv(Path(out) / "trades_is_chosen.csv", index=False)
    Path(out, "results.json").write_text(json.dumps(clean(res), indent=1))
    print("chosen", GRID[best])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
