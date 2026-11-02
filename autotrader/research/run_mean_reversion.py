import json
import time

import harness
from strategies.mean_reversion import GRID, weights

t0 = time.time()
res = harness.evaluate(
    "mean_reversion", weights, GRID, out_dir="results/mean_reversion",
    notes="Connors-style RSI(2) < x pullbacks above 200d SMA, exit close > 5d SMA or 10 days; "
          "top-100 point-in-time liquid universe; daily decisions; fixed 1/max_pos slots.")
print(harness.summary_table(res))
print(json.dumps({k: res[k] for k in ("chosen_params", "grid", "double_costs", "lookahead_check",
                                      "grid_share_beating_nifty_oos")}, indent=1, default=float))
for k in ("full_2008_2026", "in_sample_2008_2019", "out_of_sample_2020_2026"):
    print(k, res["periods"][k])
print("elapsed", round(time.time() - t0, 1))
