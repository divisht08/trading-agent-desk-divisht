"""Risk manager: position sizing and hard limits. Every entry must pass through approve()."""
from __future__ import annotations

from dataclasses import dataclass
from math import floor

from .strategies import Signal


@dataclass
class Decision:
    ok: bool
    qty: int = 0
    why: str = ""


class RiskManager:
    def __init__(self, risk_cfg: dict, capital: float):
        self.c = risk_cfg
        self.capital = capital

    def size(self, sig: Signal) -> int:
        per_share_risk = sig.entry - sig.stop
        if per_share_risk <= 0:
            return 0
        risk_amt = self.capital * self.c["risk_per_trade_pct"] / 100
        qty = floor(risk_amt / per_share_risk)
        cap_qty = floor(self.capital * self.c["max_position_pct"] / 100 / sig.entry)
        return max(0, min(qty, cap_qty))

    def approve(self, sig: Signal, positions: dict, cash: float, day_pnl: float,
                week_pnl: float, trades_today: int) -> Decision:
        c = self.c
        if sig.symbol in positions:
            return Decision(False, why="already held")
        if not (c["min_price"] <= sig.entry <= c["max_price"]):
            return Decision(False, why="price outside band")
        if day_pnl <= -self.capital * c["daily_loss_limit_pct"] / 100:
            return Decision(False, why="daily loss limit hit")
        if week_pnl <= -self.capital * c["weekly_loss_limit_pct"] / 100:
            return Decision(False, why="weekly loss limit hit")
        if trades_today >= c["max_trades_per_day"]:
            return Decision(False, why="max trades/day")
        if len(positions) >= c["max_open_positions"]:
            return Decision(False, why="max open positions")
        qty = self.size(sig)
        if qty < 1:
            return Decision(False, why="size < 1 share")
        cost = qty * sig.entry
        used = {"MIS": 0.0, "CNC": 0.0}
        for p in positions.values():
            used[p["product"]] += p["qty"] * p["entry"]
        limit = (c["max_intraday_exposure_pct"] if sig.product == "MIS"
                 else c["max_swing_exposure_pct"]) / 100 * self.capital
        if used[sig.product] + cost > limit:
            qty = floor((limit - used[sig.product]) / sig.entry)
            if qty < 1:
                return Decision(False, why=f"{sig.product} exposure limit")
            cost = qty * sig.entry
        if cost > cash:
            qty = floor(cash / sig.entry)
            if qty < 1:
                return Decision(False, why="insufficient cash")
        return Decision(True, qty, "ok")

    def halted(self, day_pnl: float, week_pnl: float) -> bool:
        return (day_pnl <= -self.capital * self.c["daily_loss_limit_pct"] / 100
                or week_pnl <= -self.capital * self.c["weekly_loss_limit_pct"] / 100)
