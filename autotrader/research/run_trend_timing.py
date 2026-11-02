import json

import harness
import sys
from strategies.trend_timing import GRID, GRID_TV, weights

NOTES = ("Equal-weight top-N liquid basket, monthly rebalance, fully invested only when the market "
         "(Nifty chained with an EW top-50 proxy where Nifty data is missing) is in an uptrend. "
         "Grid: basket_n {30,50} x signal {sma200_daily, sma200_monthly, sma10m, tsmom12, tsmom6, combo}.")

NOTES_TV = ("TRIAL 2 (declared after trial 1): same timing grid, basket weighted by 60-day median traded "
            "value, capped at 10% per stock. Lower in-sample Sharpe than trial 1, so trial 1 is the reported result.")

if __name__ == "__main__":
    # usage: python3 run_trend_timing.py [tv]
    #   default: trial 1 (equal weight), the reported result -> results/trend_timing/
    #   tv:      trial 2 (traded-value weighted, 10% cap)     -> results/trend_timing/trial2_tv_capped/
    if len(sys.argv) > 1 and sys.argv[1] == "tv":
        res = harness.evaluate("trend_timing", weights, GRID_TV, out_dir="results/trend_timing/trial2_tv_capped", notes=NOTES_TV)
    else:
        res = harness.evaluate("trend_timing", weights, GRID, out_dir="results/trend_timing", notes=NOTES)
    print(json.dumps({k: res[k] for k in ("chosen_params", "lookahead_check", "grid_share_beating_nifty_oos")}, indent=1))
    print(harness.summary_table(res))
    for g in res["grid"]:
        print(g)
    for k in ("full_2008_2026", "in_sample_2008_2019", "out_of_sample_2020_2026"):
        print(k, res["periods"][k])
        print("  nifty", res["benchmarks"]["nifty50"][k])
        print("  ew100", res["benchmarks"]["equal_weight_top100"][k])
    print("2x costs", res["double_costs"])
