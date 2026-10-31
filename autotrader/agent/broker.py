"""Brokers. PaperBroker simulates fills on live prices; KiteBroker places real orders.

KiteBroker refuses to construct unless Config.live_enabled() is true.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import pandas as pd


class Broker(ABC):
    name = "base"

    @abstractmethod
    def ltp(self, symbols: list[str]) -> dict[str, float]: ...

    @abstractmethod
    def history(self, symbol: str, interval: str, days: int) -> pd.DataFrame:
        """Columns: date, open, high, low, close, volume (ascending)."""

    @abstractmethod
    def order(self, symbol: str, side: str, qty: int, product: str,
              ref_price: float) -> float:
        """Place a market order, return average fill price. Raise on failure."""


class DataSource(ABC):
    """Market data provider (Kite Connect). PaperBroker wraps one."""

    @abstractmethod
    def ltp(self, symbols: list[str]) -> dict[str, float]: ...

    @abstractmethod
    def history(self, symbol: str, interval: str, days: int) -> pd.DataFrame: ...


class PaperBroker(Broker):
    name = "paper"

    def __init__(self, data: DataSource, costs: dict[str, float]):
        self.data = data
        self.costs = costs

    def ltp(self, symbols):
        return self.data.ltp(symbols)

    def history(self, symbol, interval, days):
        return self.data.history(symbol, interval, days)

    def order(self, symbol, side, qty, product, ref_price):
        slip = self.costs.get("slippage_pct", 0.05) / 100
        return ref_price * (1 + slip if side == "BUY" else 1 - slip)

    def fee(self, price: float, qty: int) -> float:
        c = self.costs
        return price * qty * (c.get("brokerage_pct", 0.03) + c.get("statutory_pct", 0.06)) / 100


class KiteBroker(Broker):
    name = "kite"

    def __init__(self, kite: Any, instruments: dict[str, int], cfg):
        if not cfg.live_enabled():
            raise RuntimeError("Live trading is locked. See README 'Going live'.")
        self.kite = kite
        self.tokens = instruments

    def ltp(self, symbols):
        keys = [f"NSE:{s}" for s in symbols]
        out = self.kite.ltp(keys)
        return {k.split(":")[1]: v["last_price"] for k, v in out.items()}

    def history(self, symbol, interval, days):
        return kite_history(self.kite, self.tokens, symbol, interval, days)

    def order(self, symbol, side, qty, product, ref_price):
        oid = self.kite.place_order(
            variety="regular", exchange="NSE", tradingsymbol=symbol,
            transaction_type=side, quantity=qty, product=product,
            order_type="MARKET", validity="DAY", tag="agent")
        for _ in range(20):                      # poll up to ~20s for the fill
            time.sleep(1)
            hist = self.kite.order_history(oid)
            last = hist[-1]
            if last["status"] == "COMPLETE":
                return float(last["average_price"])
            if last["status"] in ("REJECTED", "CANCELLED"):
                raise RuntimeError(f"{symbol} {side} {last['status']}: {last.get('status_message')}")
        try:
            self.kite.cancel_order("regular", oid)
        except Exception:
            pass
        raise RuntimeError(f"{symbol} {side} not filled in time; cancelled")

    def positions_net(self) -> dict[str, int]:
        return {p["tradingsymbol"]: p["quantity"] for p in self.kite.positions()["net"]
                if p["quantity"] != 0}

    def holdings_qty(self) -> dict[str, int]:
        return {h["tradingsymbol"]: h["quantity"] for h in self.kite.holdings()}

    def cash(self) -> float:
        return float(self.kite.margins("equity")["available"]["live_balance"])


_INTERVALS = {"5m": "5minute", "15m": "15minute", "1d": "day"}


def kite_history(kite, tokens: dict[str, int], symbol: str, interval: str, days: int) -> pd.DataFrame:
    time.sleep(0.35)  # Kite historical API limit is ~3 requests/second
    to = datetime.now()
    frm = to - pd.Timedelta(days=days)
    rows = kite.historical_data(tokens[symbol], frm, to, _INTERVALS[interval])
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df[["date", "open", "high", "low", "close", "volume"]]


class KiteData(DataSource):
    """Read-only market data via Kite Connect (used by paper mode)."""

    def __init__(self, kite: Any):
        self.kite = kite
        self.tokens = {i["tradingsymbol"]: i["instrument_token"]
                       for i in kite.instruments("NSE") if i["segment"] == "NSE"}

    def ltp(self, symbols):
        out = self.kite.ltp([f"NSE:{s}" for s in symbols])
        return {k.split(":")[1]: v["last_price"] for k, v in out.items()}

    def history(self, symbol, interval, days):
        return kite_history(self.kite, self.tokens, symbol, interval, days)
