"""Stress test: run strategy variants through India's bad market periods and compare with Nifty.

Parameters are the untuned defaults from config.yaml - deliberately NOT optimised on these periods.

Usage:
  python -m agent.stress_test <daily_dir> <nifty_csv> [out.json]
  daily_dir: one CSV per symbol (lower-case file names), columns Date,Open,High,Low,Close,Volume
  nifty_csv: Nifty 50 index daily history with Date,Close columns
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from . import backtest, backtest_momentum
from .config import Config

PERIODS = {
    "2008 global financial crisis": ("2008-01-01", "2009-01-01"),
    "2011 slowdown": ("2011-01-01", "2012-01-01"),
    "2015-16 correction": ("2015-03-01", "2016-03-01"),
    "2018 mid/small-cap crash": ("2018-01-01", "2019-01-01"),
    "2020 COVID year": ("2020-01-01", "2021-01-01"),
    "2020 crash only (Jan-Mar)": ("2020-01-15", "2020-04-01"),
    "2022 rate hikes": ("2022-01-01", "2023-01-01"),
    "2024-25 correction": ("2024-09-27", "2025-04-01"),
}

VARIANTS = {
    "Swing - as built": ("swing", {}, {"min_position_fraction": 0}),
    "Swing + ranking + min size": ("swing", {"rank": "momentum"}, {"min_position_fraction": 0.25}),
    "Swing + ranking + min size + regime": ("swing", {"rank": "momentum", "regime": "breadth"},
                                            {"min_position_fraction": 0.25}),
    "Momentum rotation (top 5)": ("momentum", {}, {}),
    "Momentum rotation + regime": ("momentum", {"regime": "breadth"}, {}),
}


def load_symbol_dir(path: str, symbols: list[str]) -> dict[str, pd.DataFrame]:
    out = {}
    for s in symbols:
        f = Path(path) / f"{s.lower()}.csv"
        if not f.exists():
            continue
        df = pd.read_csv(f, usecols=["Date", "Open", "High", "Low", "Close", "Volume"])
        df.columns = [c.lower() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"])
        df = df.dropna().sort_values("date").drop_duplicates("date").reset_index(drop=True)
        out[s] = df
    return out


def load_index(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, usecols=["Date", "Close"])
    df.columns = ["date", "close"]
    df["date"] = pd.to_datetime(df["date"])
    return df.dropna().sort_values("date").reset_index(drop=True)


def run_all(data, nifty, cfg, periods=PERIODS, variants=VARIANTS) -> dict:
    results = {}
    for pname, (start, end) in periods.items():
        row = {"Nifty buy & hold": backtest.buy_hold(nifty, start, end)}
        for vname, (kind, params, risk_over) in variants.items():
            c = Config({**cfg.raw, "risk": {**cfg.raw["risk"], **risk_over}})
            mod = backtest if kind == "swing" else backtest_momentum
            row[vname] = mod.run(data, c, params, start, end)
        results[pname] = row
        print(f"\n== {pname} ({start} to {end})")
        for k, v in row.items():
            print(f"  {k:38} return {v.get('return_pct'):>7}%  maxDD {v.get('max_drawdown_pct'):>7}%  "
                  f"sharpe {v.get('sharpe'):>5}  trades {v.get('trades', '-')}", flush=True)
    return results


def main():
    cfg = Config.load()
    data = load_symbol_dir(sys.argv[1], cfg["universe"])
    nifty = load_index(sys.argv[2])
    res = run_all(data, nifty, cfg)
    if len(sys.argv) > 3:
        Path(sys.argv[3]).write_text(json.dumps(res, indent=2, default=float))


if __name__ == "__main__":
    main()
