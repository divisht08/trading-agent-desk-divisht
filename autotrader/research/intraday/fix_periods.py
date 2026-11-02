"""Recompute chosen-params per-period stats using the equity at each period's start (from the saved trade lists)."""
import sys
sys.path.insert(0, "/home/claude/research/intraday")
import json
import pandas as pd
import orb_lib as L
from run_orb import PERIODS, IS
out = sys.argv[2]
data = L.load_bars(sys.argv[1]); cfg = L.Config.load(L.CFG_PATH); days = L.session_days(data)
r = json.load(open(f"{out}/results.json"))
for name, (a, b) in PERIODS.items():
    tr = pd.read_csv(f"{out}/trades_{'is' if a < IS[1] else 'oos'}_chosen.csv")
    run_start = IS[0] if a < IS[1] else "2019-01-01"
    eq0 = cfg.capital + tr[(tr.date >= run_start) & (tr.date < a)].pnl.sum()
    s = L.stats(tr, cfg, days, a, b, capital0=eq0)
    s["equity_at_start"] = round(float(eq0), 0)
    r["chosen"][name] = json.loads(json.dumps(s, default=lambda o: o.item()))
    print(name, s["equity_at_start"], s["total_return_pct"], s["sharpe"], s["max_dd_pct"])
r["chosen_period_note"] = ("IS/OOS runs each start at capital 100000 and compound; per-year and bad-period stats are "
                           "slices of those runs, measured from the equity at the slice start.")
open(f"{out}/results.json", "w").write(json.dumps(r, indent=1))
