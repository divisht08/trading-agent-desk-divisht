"""Daily-bar backtester for swing_trend, using the SAME swing_trend()/swing_exit() as the live engine.

No lookahead: signals and exit decisions use completed daily bars; fills happen at the NEXT
day's open (+slippage). Stops are checked against each day's low; a gap below the stop fills at
the open. Costs from config.yaml. Long-only, one position per symbol, shared capital.

Not simulated here: daily/weekly loss halts and max-trades-per-day (swing trades are infrequent).

Usage: python -m agent.backtest data/daily      # SYMBOL.csv with date,open,high,low,close,volume
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from .config import Config
from .risk import RiskManager
from .strategies import swing_exit, swing_trend


def run(data: dict[str, pd.DataFrame], cfg: Config, params: dict | None = None,
        start: str | None = None, end: str | None = None) -> dict:
    p = {**cfg["strategies"]["swing_trend"], **(params or {})}
    costs = cfg["costs"]
    cost_rate = (costs["brokerage_pct"] + costs["statutory_pct"]) / 100
    slip = costs["slippage_pct"] / 100
    capital = cfg.capital
    idx = {s: df.set_index("date") for s, df in data.items()}
    pos_of = {s: {d: i for i, d in enumerate(df.index)} for s, df in idx.items()}
    W = 290  # trailing bars per decision == what the live engine fetches (420 calendar days)

    def window(sym, day):
        i = pos_of[sym][day]
        return idx[sym].iloc[max(0, i - W + 1): i + 1].reset_index()
    dates = sorted(set().union(*[set(d["date"]) for d in data.values()]))
    dates = [d for d in dates if (not start or d >= pd.Timestamp(start)) and (not end or d < pd.Timestamp(end))]

    cash, positions, trades, equity = capital, {}, [], []
    pending_buy: dict[str, object] = {}
    pending_sell: dict[str, str] = {}

    for day in dates:
        # 1) execute yesterday's decisions at today's open
        for sym, why in list(pending_sell.items()):
            if sym in positions and day in idx[sym].index:
                o = float(idx[sym].loc[day, "open"])
                cash += _sell(positions.pop(sym), o * (1 - slip), cost_rate, why, trades, day)
            pending_sell.pop(sym, None)
        for sym, sig in list(pending_buy.items()):
            pending_buy.pop(sym)
            if sym in positions or day not in idx[sym].index:
                continue
            fill = float(idx[sym].loc[day, "open"]) * (1 + slip)
            if fill <= sig.stop:
                continue
            sig.entry = fill
            equity_now = cash + sum(q["qty"] * q["last"] for q in positions.values())
            risk = RiskManager(cfg["risk"], equity_now)
            held = {k: {"product": "CNC", "qty": v["qty"], "entry": v["entry"]} for k, v in positions.items()}
            d = risk.approve(sig, held, cash, 0, 0, 0)
            if d.ok:
                cash -= fill * d.qty * (1 + cost_rate)
                positions[sym] = {"symbol": sym, "qty": d.qty, "entry": fill, "stop": sig.stop,
                                  "peak": fill, "opened_at": str(day), "last": fill,
                                  "risk": (fill - sig.stop) * d.qty}

        # 2) intraday stop checks + end-of-day decisions on completed bar
        for sym, df in idx.items():
            if day not in df.index:
                continue
            bar = df.loc[day]
            if sym in positions:
                pos = positions[sym]
                pos["last"] = float(bar["close"])
                if float(bar["low"]) <= pos["stop"]:
                    px = min(float(bar["open"]), pos["stop"]) * (1 - slip)
                    cash += _sell(positions.pop(sym), px, cost_rate, "stop", trades, day)
                    continue
                hist = window(sym, day)
                out, trail, why = swing_exit(pos, hist, float(bar["close"]), p, pd.Timestamp(day))
                pos["stop"], pos["peak"] = max(pos["stop"], trail), max(pos["peak"], float(bar["close"]))
                if out:
                    pending_sell[sym] = why
                continue
            hist = window(sym, day)
            sig = swing_trend(sym, hist, p)
            if sig:
                pending_buy[sym] = sig

        equity.append((day, cash + sum(q["qty"] * q["last"] for q in positions.values())))

    return summarize(trades, capital, equity)


def _sell(pos, px, cost_rate, why, trades, day) -> float:
    proceeds = px * pos["qty"] * (1 - cost_rate)
    pnl = proceeds - pos["entry"] * pos["qty"] * (1 + cost_rate)
    trades.append({"symbol": pos["symbol"], "date": str(day), "pnl": pnl,
                   "r": pnl / pos["risk"] if pos["risk"] else 0, "reason": why,
                   "days": (pd.Timestamp(day) - pd.Timestamp(pos["opened_at"])).days})
    return proceeds


def summarize(trades, capital, equity) -> dict:
    if not equity:
        return {"trades": 0}
    eq = pd.Series([e for _, e in equity], index=[d for d, _ in equity])
    rets = eq.pct_change().dropna()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / capital) ** (1 / years) - 1
    t = pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl", "r", "days"])
    wins, losses = t[t.pnl > 0], t[t.pnl <= 0]
    return {
        "trades": len(t), "win_rate": round(len(wins) / len(t), 3) if len(t) else 0,
        "avg_r": round(float(t.r.mean()), 3) if len(t) else 0,
        "profit_factor": round(wins.pnl.sum() / -losses.pnl.sum(), 2) if len(losses) and losses.pnl.sum() < 0 else None,
        "avg_hold_days": round(float(t.days.mean()), 1) if len(t) else 0,
        "return_pct": round((eq.iloc[-1] / capital - 1) * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "max_drawdown_pct": round(float((eq / eq.cummax() - 1).min()) * 100, 2),
        "sharpe": round(float(rets.mean() / rets.std() * 252 ** 0.5), 2) if rets.std() > 0 else 0,
    }


def buy_hold(df: pd.DataFrame, start: str | None, end: str | None) -> dict:
    d = df[(df["date"] >= (start or df["date"].min())) & (df["date"] < (end or "2100-01-01"))]
    eq = d["close"] / d["close"].iloc[0]
    years = (d["date"].iloc[-1] - d["date"].iloc[0]).days / 365.25
    r = eq.pct_change().dropna()
    return {"return_pct": round((eq.iloc[-1] - 1) * 100, 2),
            "cagr_pct": round((eq.iloc[-1] ** (1 / years) - 1) * 100, 2),
            "max_drawdown_pct": round(float((eq / eq.cummax() - 1).min()) * 100, 2),
            "sharpe": round(float(r.mean() / r.std() * 252 ** 0.5), 2)}


def load_dir(path: str) -> dict[str, pd.DataFrame]:
    out = {}
    for f in Path(path).glob("*.csv"):
        df = pd.read_csv(f, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
        if df["date"].dt.tz is not None:
            df["date"] = df["date"].dt.tz_localize(None)
        df["date"] = df["date"].dt.normalize()
        out[f.stem] = df
    return out


if __name__ == "__main__":
    print(run(load_dir(sys.argv[1]), Config.load()))
