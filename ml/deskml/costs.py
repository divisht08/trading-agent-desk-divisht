"""Zerodha equity charges, as listed on https://zerodha.com/charges/ (checked 25 Oct 2026).

Re-check that page periodically; rates change with budgets and exchange circulars.
"""
from __future__ import annotations

TXN = {"NSE": 0.0000307, "BSE": 0.0000375}
SEBI = 10 / 1e7          # ₹10 per crore
GST = 0.18
DP_CHARGE = 15.34        # per scrip, on delivery sells


def charges(side: str, qty: int, price: float, product: str = "CNC", exchange: str = "NSE") -> float:
    """Total statutory + broker charges in rupees for one executed order."""
    turnover = qty * price
    side = side.upper()
    if product == "CNC":
        brokerage = 0.0
        stt = 0.001 * turnover
        stamp = 0.00015 * turnover if side == "BUY" else 0.0
        dp = DP_CHARGE if side == "SELL" else 0.0
    else:  # MIS intraday
        brokerage = min(20.0, 0.0003 * turnover)
        stt = 0.00025 * turnover if side == "SELL" else 0.0
        stamp = 0.00003 * turnover if side == "BUY" else 0.0
        dp = 0.0
    txn = TXN.get(exchange, TXN["NSE"]) * turnover
    sebi = SEBI * turnover
    gst = GST * (brokerage + txn + sebi)
    return round(brokerage + stt + stamp + txn + sebi + gst + dp, 2)


def slipped(side: str, price: float, bps: float) -> float:
    """Price after adverse slippage: buys fill higher, sells fill lower."""
    adj = price * bps / 10000
    return round(price + adj if side.upper() == "BUY" else price - adj, 2)
