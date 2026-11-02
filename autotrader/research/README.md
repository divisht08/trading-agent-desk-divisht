# Research: parallel strategy backtests

Results: [RESULTS.md](RESULTS.md). Independent audit: [audit/AUDIT.md](audit/AUDIT.md).

## Layout

```
harness.py           shared vectorised simulator, point-in-time liquidity universe, metrics, look-ahead check, evaluate()
build_panel.py       builds data/panel.npz from eod2 daily CSVs (all NSE equities with INE ISINs)
AGENT_BRIEF.md       rules every research agent followed
strategies/*.py      one weights() function per strategy (example_momentum, momentum_plus, mean_reversion,
                     low_vol, trend_timing, breakout_v2)
run_*.py             runs harness.evaluate() for each strategy -> results/<name>/results.json
intraday/            minute-data ORB study (resampling + replay through agent/backtest_intraday.py)
audit/               independent audit scripts and AUDIT.md
results/<name>/      results.json per strategy (daily return CSVs are not committed; re-run to regenerate)
```

## Reproduce

Data is not committed (third-party, ~600 MB). Get it from GitHub:

```
git clone --depth 1 https://github.com/BennyThadikaran/eod2_data      <DATA_SRC>/eod2
git clone --depth 1 https://github.com/kalilurrahman/NIFTY_50_STOCK_DATA <DATA_SRC>/k50
export RESEARCH_DATA_SRC=<DATA_SRC>
python3 build_panel.py <DATA_SRC>/eod2/daily data/panel.npz
python3 run_low_vol.py            # etc.
```

Some intraday and audit scripts still contain absolute paths from the machine they ran on; adjust before re-running.

## Caveats

- Survivorship bias: eod2 removes delisted stocks (see audit). Results are biased upward versus Nifty.
- Prices are split/bonus adjusted but not dividend adjusted; Nifty is the price index. Both sides exclude dividends.
- Idle cash earns 0% in the harness; the audit reports a 6%/yr cash-yield sensitivity.
