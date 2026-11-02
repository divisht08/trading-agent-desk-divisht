"""Shared, vectorised backtest harness for NSE daily strategies.

Contract for a strategy:  fn(panel: Panel, univ: pd.DataFrame[bool], **params) -> weights DataFrame
  - index = trading dates, columns = symbols, values = target portfolio weight (>= 0, row sum <= 1;
    the rest is cash). Long only.
  - The weight on date t may use ONLY data up to and including the close of t.
  - The harness executes weights decided at the close of t at the OPEN of t+1, holds open-to-open,
    and charges costs on turnover. You never shift anything yourself.

Everything is deliberately simple and conservative; see notes in README.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

import os

ROOT = Path(__file__).parent
DATA_SRC = Path(os.environ.get("RESEARCH_DATA_SRC", "/home/claude/data_src"))   # see README: where the raw data lives
PANEL_PATH = Path(os.environ.get("RESEARCH_PANEL", ROOT / "data" / "panel.npz"))
NIFTY_PATH = DATA_SRC / "k50" / "NIFTY50_stock_history.csv"

# brokerage + statutory + slippage, per side, as a fraction (0.03% + 0.06% + 0.05% = 0.14%)
COST_PER_SIDE = 0.0014
MAX_DAILY_MOVE = 0.40   # |open-to-open move| above this is treated as a data error (return set to 0)

PERIODS = {
    "full_2008_2026": ("2008-01-01", "2026-10-24"),
    "in_sample_2008_2019": ("2008-01-01", "2020-01-01"),
    "out_of_sample_2020_2026": ("2020-01-01", "2026-10-24"),
    "2008_crisis": ("2008-01-01", "2009-01-01"),
    "2011_slowdown": ("2011-01-01", "2012-01-01"),
    "2015_16_correction": ("2015-03-01", "2016-03-01"),
    "2018_midsmall_crash": ("2018-01-01", "2019-01-01"),
    "2020_covid_year": ("2020-01-01", "2021-01-01"),
    "2020_crash_jan_mar": ("2020-01-15", "2020-04-01"),
    "2022_rate_hikes": ("2022-01-01", "2023-01-01"),
    "2024_25_correction": ("2024-09-27", "2025-04-01"),
}
BAD_PERIODS = [k for k in PERIODS if k[0].isdigit()]


@dataclass
class Panel:
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.close.index

    def upto(self, cut) -> "Panel":
        """Copy of the panel truncated at `cut` (inclusive) - used by the look-ahead check."""
        cut = pd.Timestamp(cut)
        return Panel(*(getattr(self, k).loc[:cut] for k in ("open", "high", "low", "close", "volume")))


_CACHE: dict = {}


def load_panel(start: str = "2004-01-01") -> Panel:
    if "panel" not in _CACHE:
        z = np.load(PANEL_PATH, allow_pickle=False)
        idx = pd.DatetimeIndex(z["dates"].astype("datetime64[ns]"))
        cols = list(z["symbols"])
        dfs = {k: pd.DataFrame(z[k], index=idx, columns=cols) for k in ("open", "high", "low", "close", "volume")}
        _CACHE["panel"] = Panel(**dfs)
    p = _CACHE["panel"]
    return p if start is None else Panel(*(getattr(p, k).loc[start:] for k in ("open", "high", "low", "close", "volume")))


NIFTY_NSE_PATH = DATA_SRC / "eod2" / "daily" / "nifty 50.csv"


def load_nifty() -> pd.Series:
    """Nifty 50 closes: NSE's own series (Feb 2012 - Oct 2026) spliced onto an index history that
    starts Sep 2007 (the two agree to 1e-7 on every overlapping day)."""
    a = pd.read_csv(NIFTY_PATH, usecols=["Date", "Close"], parse_dates=["Date"]).dropna().set_index("Date")["Close"]
    b = pd.read_csv(NIFTY_NSE_PATH, usecols=["Date", "Close"], parse_dates=["Date"]).dropna().set_index("Date")["Close"]
    s = pd.concat([a[a.index < b.index[0]], b]).sort_index()
    return s[~s.index.duplicated(keep="last")]


def liquid_universe(panel: Panel, top_n: int = 100, lookback: int = 60, min_history: int = 250,
                    min_price: float = 20.0) -> pd.DataFrame:
    """Point-in-time universe: at each month-end, the top_n stocks by median daily traded value over
    the last `lookback` days, with >= min_history trading days and price >= min_price. Held for the
    following month. Uses only data up to each month-end; includes stocks that later fell away."""
    tv = (panel.close * panel.volume)
    med = tv.rolling(lookback, min_periods=int(lookback * 0.8)).median()
    hist = panel.close.notna().cumsum()
    ok = (hist >= min_history) & (panel.close >= min_price)
    med = med.where(ok)
    month_end = panel.dates.to_series().groupby(panel.dates.to_period("M")).max().values
    ranks = med.loc[month_end].rank(axis=1, ascending=False, method="first")
    sel = (ranks <= top_n)
    univ = sel.reindex(panel.dates).ffill().fillna(False).astype(bool)
    # a stock that stops trading drops out immediately
    return univ & panel.close.notna()


def simulate(weights: pd.DataFrame, panel: Panel, cost_per_side: float = COST_PER_SIDE) -> pd.DataFrame:
    """Decide at close t, trade at open t+1, hold open-to-open. Returns a DataFrame with columns
    ret (net daily return), gross, cost, turnover, exposure - indexed by the date the return accrues."""
    w = weights.reindex(index=panel.dates, columns=panel.close.columns).fillna(0.0).astype("float64")
    w = w.clip(lower=0)
    s = w.sum(axis=1)
    w = w.div(s.where(s > 1, 1.0), axis=0)               # never more than 100% invested
    o = panel.open.astype("float64")
    r = (o.shift(-1) / o - 1)                            # open t -> open t+1, labelled t
    r = r.where(r.abs() <= MAX_DAILY_MOVE, 0.0).fillna(0.0)
    held = w.shift(1).fillna(0.0)                        # weight decided at close t-1 is held from open t
    gross = (held * r).sum(axis=1)
    turnover = (w - w.shift(1).fillna(0.0)).abs().sum(axis=1).shift(1).fillna(0.0)
    cost = turnover * cost_per_side
    out = pd.DataFrame({"gross": gross, "cost": cost, "turnover": turnover, "exposure": held.sum(axis=1)})
    out["ret"] = out["gross"] - out["cost"]
    return out


def metrics(ret: pd.Series, start=None, end=None, turnover: pd.Series | None = None,
            exposure: pd.Series | None = None) -> dict:
    r = ret.loc[start:end] if (start or end) else ret
    if end is not None:
        r = r[r.index < pd.Timestamp(end)]
    if len(r) < 5:
        return {}
    eq = (1 + r).cumprod()
    years = max((r.index[-1] - r.index[0]).days / 365.25, 1e-9)
    total = float(eq.iloc[-1] - 1)
    out = {
        "return_pct": round(total * 100, 2),
        "cagr_pct": round(((1 + total) ** (1 / years) - 1) * 100, 2) if years >= 1 else None,
        "vol_pct": round(float(r.std() * np.sqrt(252) * 100), 2),
        "sharpe": round(float(r.mean() / r.std() * np.sqrt(252)), 2) if r.std() > 0 else 0.0,
        "max_drawdown_pct": round(float((eq / eq.cummax() - 1).min() * 100), 2),
        "pct_positive_months": round(float(((1 + r).resample("ME").prod() - 1 > 0).mean() * 100), 1),
    }
    if years >= 1:
        yr = (1 + r).resample("YE").prod() - 1
        out["worst_year_pct"] = round(float(yr.min() * 100), 2)
    if turnover is not None:
        t = turnover.loc[r.index]
        out["annual_turnover_x"] = round(float(t.sum() / years / 2), 2)   # one-way
    if exposure is not None:
        out["avg_exposure_pct"] = round(float(exposure.loc[r.index].mean() * 100), 1)
    return out


def benchmark_returns(panel: Panel) -> dict[str, pd.Series]:
    """Nifty 50 (price) on the panel calendar, and an equal-weight top-100 liquid universe."""
    n = load_nifty().reindex(panel.dates).ffill()
    nifty = n.pct_change().fillna(0.0)
    univ = liquid_universe(panel)
    ew = univ.astype(float).div(univ.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    ew = hold_from_month_end(ew)                                              # rebalance monthly
    return {"nifty50": nifty, "equal_weight_top100": simulate(ew, panel)["ret"]}


def month_end_mask(index: pd.DatetimeIndex) -> pd.Series:
    """True on the last trading day of each month (of the dates available)."""
    s = index.to_series()
    return s.groupby(index.to_period("M")).transform("max") == s


def hold_from_month_end(target: pd.DataFrame) -> pd.DataFrame:
    """Keep only month-end target rows and carry them forward: a monthly rebalance."""
    m = month_end_mask(target.index)
    t = target.copy()
    t.loc[~m.values] = np.nan
    return t.ffill().fillna(0.0)


def check_no_lookahead(fn, panel: Panel, univ: pd.DataFrame, params: dict,
                       cuts=("2012-06-29", "2019-03-29", "2024-09-30")) -> dict:
    """Recompute weights on data truncated at each cut date; weights up to the cut must be identical.
    Any difference means the strategy used future data."""
    full = fn(panel, univ, **params).reindex(index=panel.dates, columns=panel.close.columns).fillna(0.0)
    worst = 0.0
    for c in cuts:
        p2 = panel.upto(c)
        u2 = univ.loc[:pd.Timestamp(c)]
        part = fn(p2, u2, **params).reindex(index=p2.dates, columns=panel.close.columns).fillna(0.0)
        diff = (full.loc[:pd.Timestamp(c)] - part).abs().to_numpy().max()
        worst = max(worst, float(diff))
    return {"passed": worst < 1e-9, "max_weight_difference": worst}


def evaluate(name: str, fn, param_grid: list[dict], out_dir: Path, panel: Panel | None = None,
             univ: pd.DataFrame | None = None, notes: str = "") -> dict:
    """Standard evaluation every agent runs:
      1. pick the param set with the best in-sample (2008-2019) Sharpe from a SMALL pre-declared grid
      2. report that set out-of-sample (2020-2026), full cycle, each bad period, and at 2x costs
      3. report OOS for every grid point (robustness) and run the look-ahead check
    """
    panel = panel or load_panel()
    univ = liquid_universe(panel) if univ is None else univ
    bench = benchmark_returns(panel)
    rows = []
    for params in param_grid:
        sim = simulate(fn(panel, univ, **params), panel)
        is_m = metrics(sim["ret"], *PERIODS["in_sample_2008_2019"])
        oos_m = metrics(sim["ret"], *PERIODS["out_of_sample_2020_2026"])
        rows.append({"params": params, "in_sample": is_m, "out_of_sample": oos_m, "_sim": sim})
    best = max(rows, key=lambda r: r["in_sample"].get("sharpe", -9))
    sim = best["_sim"]
    sim2x = simulate(fn(panel, univ, **best["params"]), panel, COST_PER_SIDE * 2)
    res = {
        "strategy": name, "notes": notes, "chosen_params": best["params"],
        "periods": {k: metrics(sim["ret"], s, e, sim["turnover"], sim["exposure"]) for k, (s, e) in PERIODS.items()},
        "benchmarks": {b: {k: metrics(r, s, e) for k, (s, e) in PERIODS.items()} for b, r in bench.items()},
        "double_costs": {k: metrics(sim2x["ret"], *PERIODS[k]) for k in ("full_2008_2026", "out_of_sample_2020_2026")},
        "grid": [{"params": r["params"], "in_sample_sharpe": r["in_sample"].get("sharpe"),
                  "oos_sharpe": r["out_of_sample"].get("sharpe"), "oos_cagr_pct": r["out_of_sample"].get("cagr_pct")}
                 for r in rows],
        "lookahead_check": check_no_lookahead(fn, panel, univ, best["params"]),
    }
    oos = [g["oos_sharpe"] for g in res["grid"] if g["oos_sharpe"] is not None]
    nifty_oos = res["benchmarks"]["nifty50"]["out_of_sample_2020_2026"]["sharpe"]
    res["grid_share_beating_nifty_oos"] = round(sum(x > nifty_oos for x in oos) / max(len(oos), 1), 2)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(res, indent=2, default=float))
    sim.to_csv(out_dir / "daily_returns.csv")
    return res


def summary_table(res: dict) -> str:
    """Markdown table: strategy vs Nifty for each period."""
    lines = ["| Period | Strategy return % | Max DD % | Sharpe | Nifty return % | Nifty max DD % |", "|---|---|---|---|---|---|"]
    for k in PERIODS:
        s, n = res["periods"].get(k, {}), res["benchmarks"]["nifty50"].get(k, {})
        lines.append(f"| {k} | {s.get('return_pct')} | {s.get('max_drawdown_pct')} | {s.get('sharpe')} | "
                     f"{n.get('return_pct')} | {n.get('max_drawdown_pct')} |")
    return "\n".join(lines)
