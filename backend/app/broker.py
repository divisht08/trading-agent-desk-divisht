"""Order execution and the books.

Two books share one schema:
  test  - simulated fills on live prices, starts with PAPER_CAPITAL
  live  - real Kite orders, budget capped at LIVE_CAPITAL

The live book only ever holds what the agent itself bought. It never touches
holdings you bought yourself; reconcile() flags it if Kite shows fewer shares
than the book (for example because you sold some manually).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from deskml.costs import charges, slipped

from .config import settings
from .store import Store, ts

log = logging.getLogger(__name__)
ORDER_TAG = "kagent"


@dataclass
class Fill:
    ok: bool
    qty: int = 0
    price: float = 0.0
    charges: float = 0.0
    order_id: str | None = None
    status: str = ""
    note: str = ""


class Ledger:
    def __init__(self, store: Store, book: str, capital: float):
        self.store, self.book = store, book
        if store.get(f"cash:{book}") is None:
            self.reset(capital)

    def reset(self, capital: float) -> None:
        self.store.set(f"cash:{self.book}", capital)
        self.store.set(f"realized:{self.book}", 0.0)
        self.store.set(f"start_capital:{self.book}", capital)
        self.store.execute("DELETE FROM positions WHERE book=?", (self.book,))
        self.store.execute("DELETE FROM equity WHERE book=?", (self.book,))

    @property
    def cash(self) -> float:
        return float(self.store.get(f"cash:{self.book}", 0.0))

    @property
    def realized(self) -> float:
        return float(self.store.get(f"realized:{self.book}", 0.0))

    @property
    def start_capital(self) -> float:
        return float(self.store.get(f"start_capital:{self.book}", 0.0))

    def positions(self) -> list[dict]:
        return self.store.query("SELECT * FROM positions WHERE book=? AND qty != 0 ORDER BY symbol", (self.book,))

    def position(self, symbol: str, exchange: str) -> dict | None:
        return self.store.one("SELECT * FROM positions WHERE book=? AND symbol=? AND exchange=? AND qty != 0",
                              (self.book, symbol, exchange))

    def apply(self, symbol: str, exchange: str, sector: str, side: str, qty: int, price: float, fee: float,
              product: str, stop: float | None = None, target: float | None = None) -> float | None:
        """Update cash and positions for one fill. Returns realised P&L when the fill reduces a position."""
        pos = self.position(symbol, exchange)
        cur = pos["qty"] if pos else 0
        avg = pos["avg_price"] if pos else 0.0
        open_fees = (pos["open_fees"] or 0.0) if pos else 0.0
        signed = qty if side == "BUY" else -qty
        self.store.set(f"cash:{self.book}", round(self.cash - signed * price - fee, 2))
        new = cur + signed
        realized = None
        if cur == 0 or (cur > 0) == (signed > 0):          # opening or adding
            new_avg = (abs(cur) * avg + qty * price) / abs(new)
            open_fees += fee
        else:                                               # reducing or closing
            closed = min(abs(cur), qty)
            share = open_fees * closed / abs(cur)
            open_fees -= share
            realized = round((1 if cur > 0 else -1) * (price - avg) * closed - fee - share, 2)
            self.store.set(f"realized:{self.book}", round(self.realized + realized, 2))
            new_avg = avg if new != 0 and (new > 0) == (cur > 0) else price
            if new != 0 and (new > 0) != (cur > 0):        # flipped through zero
                open_fees = 0.0
        if new == 0:
            self.store.execute("DELETE FROM positions WHERE book=? AND symbol=? AND exchange=?",
                               (self.book, symbol, exchange))
        else:
            self.store.execute(
                "INSERT INTO positions(book,symbol,exchange,sector,qty,avg_price,product,opened,stop,target,open_fees) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(book,symbol,exchange) DO UPDATE SET qty=excluded.qty, "
                "avg_price=excluded.avg_price, product=excluded.product, open_fees=excluded.open_fees, "
                "stop=COALESCE(excluded.stop, positions.stop), target=COALESCE(excluded.target, positions.target)",
                (self.book, symbol, exchange, sector, new, round(new_avg, 4), product,
                 pos["opened"] if pos else ts(), stop, target, round(open_fees, 2)))
        return realized

    def set_qty(self, symbol: str, exchange: str, qty: int) -> None:
        if qty <= 0:
            self.store.execute("DELETE FROM positions WHERE book=? AND symbol=? AND exchange=?",
                               (self.book, symbol, exchange))
        else:
            self.store.execute("UPDATE positions SET qty=? WHERE book=? AND symbol=? AND exchange=?",
                               (qty, self.book, symbol, exchange))


class PaperBroker:
    book = "test"

    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def execute(self, symbol: str, exchange: str, side: str, qty: int, ref_price: float, product: str) -> Fill:
        price = slipped(side, ref_price, settings.slippage_bps)
        fee = charges(side, qty, price, product, exchange)
        if side == "BUY" and qty * price + fee > self.ledger.cash:
            return Fill(False, status="REJECTED", note="Not enough test cash")
        return Fill(True, qty, price, fee, None, "COMPLETE", f"test fill, {settings.slippage_bps:g} bps slippage")


class LiveBroker:
    """Real orders through Kite Connect. The engine only calls this in Automatic mode,
    which the API only allows after the two-step check."""

    book = "live"

    def __init__(self, ledger: Ledger, gateway):
        self.ledger, self.gw = ledger, gateway

    def preflight(self) -> tuple[bool, str]:
        if getattr(self.gw, "demo", False):
            return False, "demo data mode"
        if not settings.live_trading_enabled:
            return False, "LIVE_TRADING_ENABLED is not true on the server"
        if not settings.live_capital:
            return False, "LIVE_CAPITAL is not set on the server"
        return True, ""

    def execute(self, symbol: str, exchange: str, side: str, qty: int, ref_price: float, product: str) -> Fill:
        ok, why = self.preflight()
        if not ok:
            return Fill(False, status="BLOCKED", note=f"Live order blocked: {why}")
        try:
            if side == "BUY":
                need = qty * ref_price * 1.01
                if need > self.ledger.cash:
                    return Fill(False, status="REJECTED", note=f"Live budget left ₹{self.ledger.cash:,.0f} is too low")
                avail = float(self.gw.margins().get("net", 0))
                if need > avail:
                    return Fill(False, status="REJECTED", note=f"Kite funds ₹{avail:,.0f} are too low")
            else:
                pos = self.ledger.position(symbol, exchange)
                if not pos or pos["qty"] < qty:
                    if not (settings.allow_intraday_shorts and product == "MIS"):
                        return Fill(False, status="REJECTED", note="Agent does not hold that many shares")
            order_id = self.gw.place_order(
                variety="regular", exchange=exchange, tradingsymbol=symbol, transaction_type=side,
                quantity=qty, product=product, order_type="MARKET", market_protection=-1, tag=ORDER_TAG)
        except Exception as e:  # noqa: BLE001
            return Fill(False, status="ERROR", note=str(e)[:200])
        for _ in range(20):
            time.sleep(0.5)
            hist = self.gw.order_history(order_id)
            last = hist[-1] if hist else {}
            st = last.get("status")
            if st == "COMPLETE":
                px = float(last.get("average_price") or ref_price)
                fq = int(last.get("filled_quantity") or qty)
                return Fill(True, fq, px, charges(side, fq, px, product, exchange), order_id, st, "live fill")
            if st in {"REJECTED", "CANCELLED"}:
                return Fill(False, 0, 0, 0, order_id, st, last.get("status_message") or st)
        return Fill(False, 0, 0, 0, order_id, "PENDING", "Not filled within 10 s - check Kite")

    def reconcile(self) -> list[str]:
        """Compare the agent's live book with what Kite shows. Flags gaps; never sells or edits by itself.

        Kite total = holdings (settled + T1) + today's net position quantity. If you sold
        shares by hand, the agent's later SELL would simply be rejected by Kite.
        """
        ok, _ = self.preflight()
        if not ok or not self.ledger.positions():
            return []
        held: dict[tuple[str, str], int] = {}
        for h in self.gw.holdings():
            k = (h["tradingsymbol"], h["exchange"])
            held[k] = held.get(k, 0) + int(h.get("quantity", 0)) + int(h.get("t1_quantity", 0))
        for p in self.gw.positions().get("net", []):
            k = (p["tradingsymbol"], p["exchange"])
            held[k] = held.get(k, 0) + int(p.get("quantity", 0))
        notes = []
        for pos in self.ledger.positions():
            have = held.get((pos["symbol"], pos["exchange"]), 0)
            if pos["qty"] > 0 and have < pos["qty"]:
                notes.append(f"{pos['symbol']}: Kite shows {have} shares, the agent's book says {pos['qty']}. "
                             "Check Kite; use 'Fix book' in Positions if you sold them yourself.")
        return notes
