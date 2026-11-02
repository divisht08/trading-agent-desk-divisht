"""Run the standard harness evaluation for momentum_plus."""
import json
import time

import harness
from strategies.momentum_plus import GRID, weights

if __name__ == "__main__":
    t0 = time.time()
    res = harness.evaluate(
        "momentum_plus", weights, GRID, out_dir="results/momentum_plus",
        notes="Risk-adjusted (mom/vol) momentum, skip 1 month, top 10/20 inverse-vol weighted, monthly; "
              "optional trend filter on EW top-100 proxy and Barroso-Santa-Clara vol targeting (15%).")
    print(harness.summary_table(res))
    print(json.dumps({k: res[k] for k in ("chosen_params", "grid", "lookahead_check", "double_costs",
                                          "grid_share_beating_nifty_oos")}, indent=1))
    print(json.dumps(res["periods"]["full_2008_2026"]), json.dumps(res["periods"]["out_of_sample_2020_2026"]))
    print(f"{time.time() - t0:.0f}s")
