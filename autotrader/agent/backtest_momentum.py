"""Monthly momentum rotation backtester (daily bars).

Logic: at each month-end close, rank the universe by momentum_score (return from ~6 months ago to
~1 month ago). Hold the top `top_n` names with a positive score, equal weight. Optional regime
filter: if market breadth (share of stocks above their EMA200) is below the threshold, hold cash.

No look-ahead: the decision uses completed month-end bars; trades fill at the NEXT day's open
(+slippage), costs on both sides. Names that stay in the top list are kept (no re-weighting) to
save costs.
"""
from __future__ import annotations

import pandas as pd

from .backtest import market_breadth, summarize
from .config import Config
from .strategies import momentum_score

DEFAULTS = {"top_n": 5, "lookback": 126, "skip": 21, "min_history": 260,
            "regime": "none", "regime_threshold": 0.5}


def run(data: dict[str, pd.DataFrame], cfg: Config, params: dict | None = None,
        start: str | None = None, end: str | None = None) -> dict:
    p = {**DEFAULTS, **(params or {})}
    costs = cfg["costs"]
    cost_rate = (costs["brokerage_pct"] + costs["statutory_pct"]) / 100
    slip = costs["slippage_pct"] / 100
    capital = cfg.capital
    idx = {s: df.set_index("date") for s, df in data.items()}
    pos_of = {s: {d: i for i, d in enumerate(df.index)} for s, df in idx.items()}
    breadth = market_breadth(idx) if p["regime"] == "breadth" else {}

    all_dates = sorted(set().union(*[set(d.index) for d in idx.values()]))
    dates = [d for d in all_dates if (not start or d >= pd.Timestamp(start)) and (not end or d < pd.Timestamp(end))]
    month_end = {d for d, nxt in zip(dates, dates[1:] + [None]) if nxt is None or nxt.month != d.month}

    cash, holdings, trades, equity = capital, {}, [], []
    target: set[str] | None = None

    for day in dates:
        # 1) rebalance at today's open to the target decided at the last month-end close
        if target is not None:
            for sym in [s for s in holdings if s not in target]:
                if day in idx[sym].index:
                    h = holdings.pop(sym)
                    px = float(idx[sym].loc[day, "open"]) * (1 - slip)
                    proceeds = px * h["qty"] * (1 - cost_rate)
                    pnl = proceeds - h["entry"] * h["qty"] * (1 + cost_rate)
                    cash += proceeds
                    trades.append({"symbol": sym, "pnl": pnl, "r": pnl / (h["entry"] * h["qty"]),
                                   "days": (day - h["opened"]).days, "reason": "rotation"})
            eq_now = cash + sum(h["qty"] * h["last"] for h in holdings.values())
            slot = eq_now / p["top_n"]
            for sym in sorted(target - set(holdings)):
                if day not in idx[sym].index:
                    continue
                fill = float(idx[sym].loc[day, "open"]) * (1 + slip)
                qty = int(min(slot, cash) / (fill * (1 + cost_rate)))
                if qty < 1:
                    continue
                cash -= fill * qty * (1 + cost_rate)
                holdings[sym] = {"qty": qty, "entry": fill, "last": fill, "opened": day}
            target = None

        # 2) mark to market
        for sym, h in holdings.items():
            if day in idx[sym].index:
                h["last"] = float(idx[sym].loc[day, "close"])
        equity.append((day, cash + sum(h["qty"] * h["last"] for h in holdings.values())))

        # 3) month-end decision on completed bars
        if day in month_end:
            if p["regime"] == "breadth" and breadth.get(day, 0.0) < p["regime_threshold"]:
                target = set()
                continue
            scores = {}
            for sym, df in idx.items():
                i = pos_of[sym].get(day)
                if i is None or i + 1 < p["min_history"]:
                    continue
                s = momentum_score(df["close"].iloc[: i + 1], p["lookback"], p["skip"])
                if s is not None and s > 0:
                    scores[sym] = s
            target = set(sorted(scores, key=scores.get, reverse=True)[: p["top_n"]])

    # report open positions at end as if sold at the last close (marked, not traded)
    return summarize(trades, capital, equity)
