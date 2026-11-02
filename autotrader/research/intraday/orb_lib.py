"""Helpers for the ORB study: load 5-min bars, call the agent's backtest_intraday.run unchanged,
capture its trade list, and compute per-period stats. Run scripts with python3 -I from this folder."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/claude/trading-agent")
sys.path.insert(0, "/home/claude/research")
import agent.backtest_intraday as bi  # noqa: E402
from agent.config import Config  # noqa: E402

CFG_PATH = "/home/claude/trading-agent/config.yaml"
_captured: dict = {}
_orig_summarize = bi.summarize


def _capture(trades, capital, daily_pnl):
    # run() is not modified; we only observe the arguments it passes to summarize()
    _captured["trades"] = list(trades)
    _captured["daily_pnl"] = dict(daily_pnl)
    return _orig_summarize(trades, capital, daily_pnl)


bi.summarize = _capture


def load_bars(path: str) -> dict[str, pd.DataFrame]:
    out = {}
    for f in sorted(Path(path).glob("*.csv")):
        df = pd.read_csv(f, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
        out[f.stem] = df
    return out


def session_days(data: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    days = set()
    for df in data.values():
        days |= set(df["date"].dt.normalize().unique())
    return pd.DatetimeIndex(sorted(days))


def run_capture(data, cfg, params, start, end):
    _captured.clear()
    summary = bi.run(data, cfg, params, start, end)
    trades = pd.DataFrame(_captured.get("trades", []))
    return summary, trades


def stats(trades: pd.DataFrame, cfg: Config, days: pd.DatetimeIndex, start: str, end: str,
          capital0: float | None = None) -> dict:
    """Stats for trades inside [start, end), compounding from capital0 (equity at start)."""
    costs = cfg["costs"]
    cost_rate = (costs["brokerage_pct"] + costs["statutory_pct"]) / 100
    slip = costs["slippage_pct"] / 100
    d = days[(days >= start) & (days < end)]
    t = trades[(trades["date"] >= start) & (trades["date"] < end)] if len(trades) else trades
    cap = capital0 or cfg.capital
    pnl = pd.Series(0.0, index=d)
    if len(t):
        pnl = pnl.add(t.groupby(pd.to_datetime(t["date"]))["pnl"].sum(), fill_value=0.0).reindex(d).fillna(0.0)
    eq = cap + pnl.cumsum()
    prev = eq.shift(fill_value=cap)
    r = pnl / prev
    n = len(d)
    total = eq.iloc[-1] / cap - 1 if n else 0.0
    yrs = n / 252
    dd = (eq / np.maximum(eq.cummax(), cap) - 1).min() if n else 0.0
    out = {"days": n, "total_return_pct": round(total * 100, 2),
           "cagr_pct": round(((1 + total) ** (1 / yrs) - 1) * 100, 2) if yrs > 0 else None,
           "sharpe": round(float(r.mean() / r.std() * np.sqrt(252)), 2) if r.std() > 0 else 0.0,
           "max_dd_pct": round(float(dd) * 100, 2), "trades": int(len(t))}
    if len(t):
        ch_i = (t["entry"] + t["exit"]) * t["qty"] * cost_rate
        sl_i = t["qty"] * (t["entry"] - t["entry"] / (1 + slip) + t["exit"] / (1 - slip) - t["exit"])
        risk_i = (t["pnl"] / t["r"]).abs()
        out["avg_cost_in_r"] = round(float(((ch_i + sl_i) / risk_i).mean()), 3)
        out["avg_r_before_costs"] = round(float(((t["pnl"] + ch_i + sl_i) / risk_i).mean()), 3)
        out["median_stop_dist_pct"] = round(float(((t["entry"] - t["stop"]) / t["entry"]).median() * 100), 3)
        out["avg_position_pct_of_capital"] = round(float((t["entry"] * t["qty"]).mean() / cap * 100), 1)
        charges, slip_cost = ch_i.sum(), sl_i.sum()
        gross = t["pnl"].sum() + charges + slip_cost
        w, l = t[t.pnl > 0], t[t.pnl <= 0]
        out.update({
            "win_rate": round(len(w) / len(t), 3), "avg_r": round(float(t["r"].mean()), 3),
            "profit_factor": round(float(w.pnl.sum() / -l.pnl.sum()), 2) if len(l) and l.pnl.sum() < 0 else None,
            "net_pnl": round(float(t["pnl"].sum()), 0), "gross_pnl_before_costs": round(float(gross), 0),
            "charges": round(float(charges), 0), "slippage_est": round(float(slip_cost), 0),
            "costs_share_of_gross_abs_pct": round(float((charges + slip_cost) / abs(gross) * 100), 1) if gross else None,
            "costs_pct_of_capital": round(float((charges + slip_cost) / cap * 100), 2),
            "exit_reasons": t["reason"].value_counts().to_dict(),
            "trades_per_day": round(len(t) / n, 2) if n else 0,
        })
    return out


def nifty_stats(start: str, end: str) -> dict:
    import harness
    s = harness.load_nifty()
    before = s[s.index < start]
    s = s[(s.index >= start) & (s.index < end)]
    base = before.iloc[-1] if len(before) else s.iloc[0]
    eq = pd.concat([pd.Series([base]), s.reset_index(drop=True)]).reset_index(drop=True)
    r = eq.pct_change().dropna()
    total = eq.iloc[-1] / base - 1
    yrs = len(s) / 252
    return {"days": int(len(s)), "total_return_pct": round(float(total) * 100, 2),
            "cagr_pct": round(((1 + total) ** (1 / yrs) - 1) * 100, 2),
            "sharpe": round(float(r.mean() / r.std() * np.sqrt(252)), 2),
            "max_dd_pct": round(float((eq / eq.cummax() - 1).min()) * 100, 2)}
