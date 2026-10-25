"""Daily-bar portfolio backtester.

Rules that keep it honest:
- Signals use data up to the close of day t; orders fill at the OPEN of day t+1.
- Every fill pays Zerodha charges (costs.py) plus slippage.
- Cash is finite; positions are sized from current equity.

What it cannot test: the Claude debate layer. An LLM already "knows" what happened
after its training data, so replaying it over history leaks the future. The
backtester tests the quantitative screen that chooses which stocks reach the desk.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from .costs import charges, slipped
from .signals import score_frame

STRATEGIES = {
    "screen": "Agent screen: buy when score ≥ entry, sell when score < exit",
    "sma_cross": "Trend: buy when 20-day crosses above 50-day average, sell on cross below",
    "rsi_reversion": "Mean reversion: buy RSI < 30 above 200-day average, sell RSI > 55",
    "breakout": "Breakout: buy new 20-day high on 1.5× volume, sell below 20-day average",
    "ml": "ML model: buy when walk-forward probability ≥ entry, sell below exit",
    "blend": "Blend: rule score mixed with the ML model, same thresholds as the agent screen",
}
ML_STRATEGIES = {"ml", "blend"}


@dataclass
class BTParams:
    strategy: str = "screen"
    start: str = ""
    end: str = ""
    capital: float = 100000.0
    max_positions: int = 10
    size_pct: float = 10.0
    stop_pct: float = 0.0
    entry_score: float = 40.0
    exit_score: float = 0.0
    entry_prob: float = 0.58
    exit_prob: float = 0.50
    ml_weight: float = 0.5
    slippage_bps: float = 5.0


def _signals(f: pd.DataFrame, p: BTParams) -> tuple[pd.Series, pd.Series]:
    if p.strategy == "sma_cross":
        up = f["sma20"] > f["sma50"]
        return up & ~up.shift(fill_value=False), ~up & up.shift(fill_value=False)
    if p.strategy == "rsi_reversion":
        return (f["rsi14"] < 30) & (f["close"] > f["sma200"]), f["rsi14"] > 55
    if p.strategy == "breakout":
        return (f["close"] > f["hi20"]) & (f["vol_ratio"] > 1.5), f["close"] < f["sma20"]
    if p.strategy == "ml":
        prob = f["prob"]
        return prob >= p.entry_prob, prob < p.exit_prob
    return f["score"] >= p.entry_score, f["score"] < p.exit_score


def run_backtest(data: dict[str, pd.DataFrame], bench: pd.Series | None, p: BTParams,
                 exchange: dict[str, str] | None = None, preds: pd.DataFrame | None = None) -> dict:
    """preds: walk-forward probabilities (date, symbol, prob). Computed here if a model strategy needs them."""
    exchange = exchange or {}
    if p.strategy in ML_STRATEGIES and preds is None:
        from .model import walk_forward
        preds = walk_forward(data, bench)
    pmap = {s: g.set_index("date")["prob"] for s, g in preds.groupby("symbol")} if preds is not None and len(preds) else {}
    start = pd.Timestamp(p.start) if p.start else None
    end = pd.Timestamp(p.end) if p.end else None
    frames, entries, exits = {}, {}, {}
    for sym, df in data.items():
        if df is None or len(df) < 120:
            continue
        f = score_frame(df, bench)
        f["open"] = df["open"]
        if p.strategy in ML_STRATEGIES:
            from .model import blend
            f["prob"] = pmap.get(sym, pd.Series(dtype=float)).reindex(f.index)
            if p.strategy == "blend":
                f["score"] = [blend(s_, None if pd.isna(pr) else pr, p.ml_weight) if not pd.isna(pr) else np.nan
                              for s_, pr in zip(f["score"], f["prob"])]
            else:
                f["score"] = (f["prob"] - 0.5) * 400
        en, ex = _signals(f, p)
        frames[sym], entries[sym], exits[sym] = f, en.fillna(False), ex.fillna(False)
    if not frames:
        return {"error": "Not enough history for any symbol (need 120+ daily bars)."}

    dates = sorted(set().union(*[f.index for f in frames.values()]))
    dates = [d for d in dates if (start is None or d >= start) and (end is None or d <= end)]
    if len(dates) < 5:
        return {"error": "Date range too short."}

    cash, pos, trades, curve = p.capital, {}, [], []
    for i, d in enumerate(dates[:-1]):
        nxt = dates[i + 1]
        # mark to market at today's close
        mv = sum(q["qty"] * float(frames[s].at[d, "close"]) if d in frames[s].index else q["qty"] * q["last"]
                 for s, q in pos.items())
        for s, q in pos.items():
            if d in frames[s].index:
                q["last"] = float(frames[s].at[d, "close"])
        equity = cash + mv
        curve.append((d, equity))

        # exits (signal, or stop hit on today's close) -> fill at next open
        for s in list(pos):
            f = frames[s]
            if d not in f.index or nxt not in f.index:
                continue
            q = pos[s]
            stop_hit = p.stop_pct and f.at[d, "close"] <= q["entry"] * (1 - p.stop_pct / 100)
            if exits[s].get(d, False) or stop_hit:
                px = slipped("SELL", float(f.at[nxt, "open"]), p.slippage_bps)
                fee = charges("SELL", q["qty"], px, "CNC", exchange.get(s, "NSE"))
                cash += q["qty"] * px - fee
                pnl = (px - q["entry"]) * q["qty"] - fee - q["fee"]
                trades.append({"symbol": s, "entry_date": q["date"].date().isoformat(), "entry": round(q["entry"], 2),
                               "exit_date": nxt.date().isoformat(), "exit": px, "qty": q["qty"],
                               "pnl": round(pnl, 2), "ret_pct": round(pnl / (q["entry"] * q["qty"]) * 100, 2),
                               "reason": "stop" if stop_hit else "signal"})
                del pos[s]

        # entries ranked by score, filled at next open
        if len(pos) < p.max_positions:
            cands = [s for s in frames if s not in pos and d in frames[s].index and nxt in frames[s].index
                     and entries[s].get(d, False)]
            cands.sort(key=lambda s: -float(frames[s].at[d, "score"]))
            for s in cands[: p.max_positions - len(pos)]:
                px = slipped("BUY", float(frames[s].at[nxt, "open"]), p.slippage_bps)
                alloc = min(equity * p.size_pct / 100, cash * 0.99)
                qty = int(math.floor(alloc / px)) if px > 0 else 0
                if qty < 1:
                    continue
                fee = charges("BUY", qty, px, "CNC", exchange.get(s, "NSE"))
                if qty * px + fee > cash:
                    continue
                cash -= qty * px + fee
                pos[s] = {"qty": qty, "entry": px, "fee": fee, "date": nxt, "last": px}

    last = dates[-1]
    mv = sum(q["qty"] * (float(frames[s].at[last, "close"]) if last in frames[s].index else q["last"])
             for s, q in pos.items())
    curve.append((last, cash + mv))
    eq = pd.Series([v for _, v in curve], index=[d for d, _ in curve])
    first_signal = None
    if p.strategy in ML_STRATEGIES and pmap:
        first_signal = str(min(s.index.min() for s in pmap.values()).date())
    return {"metrics": _metrics(eq, trades, bench, p.capital), "open_positions": len(pos),
            "model_signals_from": first_signal,
            "curve": _curve(eq, bench), "trades": trades[-300:]}


def _metrics(eq: pd.Series, trades: list[dict], bench: pd.Series | None, capital: float) -> dict:
    rets = eq.pct_change().dropna()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365)
    total = float(eq.iloc[-1]) / capital - 1
    cagr = (float(eq.iloc[-1]) / capital) ** (1 / years) - 1 if eq.iloc[-1] > 0 else -1
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0
    dd = float((eq / eq.cummax() - 1).min())
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    pf = (sum(t["pnl"] for t in wins) / -sum(t["pnl"] for t in losses)) if losses and sum(
        t["pnl"] for t in losses) < 0 else None
    out = {
        "final_equity": round(float(eq.iloc[-1]), 2), "total_return_pct": round(total * 100, 2),
        "cagr_pct": round(cagr * 100, 2), "sharpe": round(sharpe, 2), "max_drawdown_pct": round(dd * 100, 2),
        "trades": len(trades), "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "profit_factor": round(pf, 2) if pf else None,
        "avg_trade_pct": round(float(np.mean([t["ret_pct"] for t in trades])), 2) if trades else None,
        "days": len(eq),
    }
    if bench is not None and len(bench):
        b = bench.reindex(eq.index).ffill().dropna()
        if len(b) > 1:
            out["benchmark_return_pct"] = round(float(b.iloc[-1] / b.iloc[0] - 1) * 100, 2)
            out["excess_return_pct"] = round(out["total_return_pct"] - out["benchmark_return_pct"], 2)
    return out


def _curve(eq: pd.Series, bench: pd.Series | None) -> dict:
    step = max(1, len(eq) // 400)
    e = eq.iloc[::step]
    out = {"dates": [d.date().isoformat() for d in e.index], "equity": [round(float(v), 2) for v in e]}
    if bench is not None and len(bench):
        b = bench.reindex(eq.index).ffill().bfill()
        b = b / b.iloc[0] * float(eq.iloc[0])
        out["benchmark"] = [round(float(v), 2) for v in b.iloc[::step]]
    return out


def default_dates() -> tuple[str, str]:
    today = date.today()
    return date(today.year - 2, today.month, 1).isoformat(), today.isoformat()
