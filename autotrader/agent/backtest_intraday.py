"""Intraday backtester. Replays 5-minute bars through the SAME intraday_orb() the live engine uses.

No lookahead: a signal is computed on completed bars only, and the fill is the NEXT bar's open
plus slippage. Inside a bar, if both stop and target are touched, the stop is assumed first
(conservative). Everything is squared off at the configured time. Costs come from config.yaml.

Simulated: position sizing on current equity, max positions, MIS exposure cap, daily loss halt,
one trade per stock per day. Not simulated: weekly loss halt.

Usage: python -m agent.backtest_intraday data/5m     (SYMBOL.csv with date,open,high,low,close,volume)
"""
from __future__ import annotations

import sys
from datetime import time as dtime
from pathlib import Path

import pandas as pd

from .config import Config
from .risk import RiskManager
from .strategies import intraday_orb


def _t(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def run(data: dict[str, pd.DataFrame], cfg: Config, params: dict | None = None,
        start: str | None = None, end: str | None = None) -> dict:
    p = {**cfg["strategies"]["intraday_orb"], **(params or {})}
    m, costs = cfg["market"], cfg["costs"]
    cost_rate = (costs["brokerage_pct"] + costs["statutory_pct"]) / 100
    slip = costs["slippage_pct"] / 100
    risk_cfg = {**cfg["risk"]}
    capital = cfg.capital
    t_start, t_end, t_sq = _t(m["intraday_entry_start"]), _t(m["intraday_entry_end"]), _t(m["intraday_squareoff"])

    # index bars: one row per (timestamp, symbol)
    frames = []
    n_or = max(1, p["opening_range_minutes"] // 5)
    for sym, df in data.items():
        d = df.copy()
        d["symbol"] = sym
        d["i"] = range(len(d))
        # cheap pre-filter: only call the real strategy on a fresh close above the opening range
        day_key = d["date"].dt.date
        rank = d.groupby(day_key).cumcount()
        orh = d["high"].where(rank < n_or).groupby(day_key).transform("max")
        prev_close = d.groupby(day_key)["close"].shift()
        d["cand"] = (rank >= n_or) & (d["close"] > orh) & (prev_close <= orh)
        frames.append(d)
    allbars = pd.concat(frames).sort_values(["date", "symbol"])
    if start:
        allbars = allbars[allbars["date"] >= start]
    if end:
        allbars = allbars[allbars["date"] < end]

    equity = capital
    trades: list[dict] = []
    daily_pnl: dict[str, float] = {}
    pending: dict[str, object] = {}   # symbol -> Signal to fill at next bar open
    open_pos: dict[str, dict] = {}
    traded_today: set[str] = set()
    cur_day = None
    risk = RiskManager(risk_cfg, capital)

    def close(sym, price, reason, ts):
        nonlocal equity
        pos = open_pos.pop(sym)
        fill = price * (1 - slip)
        pnl = (fill - pos["entry"]) * pos["qty"] - (fill + pos["entry"]) * pos["qty"] * cost_rate
        equity += pnl
        day = str(ts.date())
        daily_pnl[day] = daily_pnl.get(day, 0.0) + pnl
        trades.append({"symbol": sym, "date": day, "pnl": pnl, "r": pnl / pos["risk"], "reason": reason})

    for ts, group in allbars.groupby("date", sort=True):
        day = ts.date()
        if day != cur_day:
            for sym in list(open_pos):              # safety: nothing carries overnight
                last = data[sym][data[sym]["date"].dt.date == cur_day].iloc[-1]
                close(sym, float(last["close"]), "eod", pd.Timestamp(cur_day))
            cur_day, pending, traded_today = day, {}, set()
            risk = RiskManager(risk_cfg, equity)     # size on current equity
        day_pnl = daily_pnl.get(str(day), 0.0)
        halted = day_pnl <= -equity * risk_cfg["daily_loss_limit_pct"] / 100

        for _, bar in group.iterrows():
            sym = bar["symbol"]
            # 1) fill pending entry at this bar's open
            if sym in pending:
                sig = pending.pop(sym)
                if not halted and ts.time() < t_sq:
                    fill = float(bar["open"]) * (1 + slip)
                    if fill > sig.stop:
                        sig.entry = fill
                        positions = {k: {"product": "MIS", "qty": v["qty"], "entry": v["entry"]}
                                     for k, v in open_pos.items()}
                        d = risk.approve(sig, positions, equity, day_pnl, 0, len(traded_today))
                        if d.ok:
                            open_pos[sym] = {"entry": fill, "qty": d.qty, "stop": sig.stop,
                                             "target": fill + p["target_r"] * (fill - sig.stop),
                                             "risk": (fill - sig.stop) * d.qty}
                            traded_today.add(sym)
            # 2) manage open position within this bar
            if sym in open_pos:
                pos = open_pos[sym]
                if bar["low"] <= pos["stop"]:
                    close(sym, min(float(bar["open"]), pos["stop"]), "stop", ts)
                elif bar["high"] >= pos["target"]:
                    close(sym, max(float(bar["open"]), pos["target"]), "target", ts)
                elif ts.time() >= t_sq or halted:
                    close(sym, float(bar["close"]), "squareoff", ts)
                continue
            # 3) look for a new signal on completed bars (this bar just closed)
            if (not bar["cand"] or halted or sym in traded_today or not (t_start <= ts.time() <= t_end)):
                continue
            hist = data[sym].iloc[max(0, bar["i"] - 450): bar["i"] + 1]
            sig = intraday_orb(sym, hist, p, ts)
            if sig:
                pending[sym] = sig

    return summarize(trades, capital, daily_pnl)


def summarize(trades: list[dict], capital: float, daily_pnl: dict[str, float]) -> dict:
    if not trades:
        return {"trades": 0, "net_pnl": 0.0, "return_pct": 0.0}
    t = pd.DataFrame(trades)
    wins = t[t.pnl > 0]
    losses = t[t.pnl <= 0]
    eq = capital + pd.Series(daily_pnl).sort_index().cumsum()
    dd = (eq / eq.cummax().clip(lower=capital) - 1).min()
    d = pd.Series(daily_pnl)
    sharpe = (d.mean() / d.std() * (252 ** 0.5)) if d.std() > 0 else 0.0
    return {
        "trades": len(t), "win_rate": round(len(wins) / len(t), 3),
        "avg_r": round(t.r.mean(), 3),
        "profit_factor": round(wins.pnl.sum() / -losses.pnl.sum(), 2) if len(losses) and losses.pnl.sum() < 0 else float("inf"),
        "net_pnl": round(t.pnl.sum(), 0), "return_pct": round(t.pnl.sum() / capital * 100, 2),
        "max_drawdown_pct": round(float(dd) * 100, 2), "sharpe": round(float(sharpe), 2),
        "days": len(d),
    }


def load_dir(path: str) -> dict[str, pd.DataFrame]:
    out = {}
    for f in Path(path).glob("*.csv"):
        df = pd.read_csv(f, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
        df["date"] = df["date"].dt.tz_localize(None) if df["date"].dt.tz is not None else df["date"]
        out[f.stem] = df
    return out


if __name__ == "__main__":
    print(run(load_dir(sys.argv[1]), Config.load()))
