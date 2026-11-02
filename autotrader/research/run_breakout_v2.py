"""Run the standard evaluation for breakout_v2 (trial 1, grid declared in strategies/breakout_v2.py)."""
import harness
from strategies.breakout_v2 import weights, GRID

NOTES = ("Donchian/Turtle breakout on PIT top-100: enter on N-day closing high above EMA200 with breadth "
         "regime on; ATR vol-parity weights (10bp/day risk, cap 10%, max 20 names), fixed for the trade; "
         "exit on close below prior M-day low (or all-cash when regime off, if regime_mode='all').")

if __name__ == "__main__":
    res = harness.evaluate("breakout_v2", weights, GRID, out_dir="results/breakout_v2", notes=NOTES)
    print(harness.summary_table(res))
    print("chosen", res["chosen_params"], "lookahead", res["lookahead_check"])
    for g in res["grid"]:
        print(g)
    import json
    print(json.dumps({k: res["periods"][k] for k in ("full_2008_2026", "in_sample_2008_2019", "out_of_sample_2020_2026")}, indent=1))
    print(json.dumps(res["double_costs"]), res["grid_share_beating_nifty_oos"])
    print(json.dumps(res["benchmarks"]["nifty50"]["out_of_sample_2020_2026"]))
