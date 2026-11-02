import json
import sys

sys.path.insert(0, "strategies")
import harness
from low_vol import GRID, weights

res = harness.evaluate(
    "low_vol", weights, GRID, out_dir="results/low_vol",
    notes="Low-volatility (inverse-vol weighted) top-N of point-in-time top-100 liquid, monthly; "
          "optional 52-week-high proximity filter (short basket -> cash). Trial 1 of max 3.")
print(json.dumps({k: res[k] for k in ("chosen_params", "grid", "lookahead_check", "double_costs",
                                       "grid_share_beating_nifty_oos")}, indent=1))
print(harness.summary_table(res))
for k in ("full_2008_2026", "in_sample_2008_2019", "out_of_sample_2020_2026"):
    print(k, res["periods"][k])
    print("  nifty", res["benchmarks"]["nifty50"][k])
    print("  ew100", res["benchmarks"]["equal_weight_top100"][k])
