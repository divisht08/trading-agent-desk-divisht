"""Market data + session gateways.

KiteGateway  - real Kite Connect (needs the paid Connect plan for quotes/history).
DemoGateway  - synthetic prices so the app can be tried without a Kite account.
               Everything it produces is flagged demo=True and the UI says DEMO.
"""
from __future__ import annotations

import hashlib
import logging
import math
import random
import time
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .config import settings
from .store import IST, Store, now_ist, ts

log = logging.getLogger(__name__)

NIFTY50_KEY = "NSE:NIFTY 50"
NIFTY50_TOKEN = 256265  # Kite instrument token for the NIFTY 50 index


class SessionMissing(RuntimeError):
    """Raised when Kite has no valid access token for today."""


def _session_valid_until(issued: datetime) -> datetime:
    """Kite access tokens are flushed around 06:00 IST the next morning."""
    nxt = (issued + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    if issued.hour < 6:
        nxt = issued.replace(hour=6, minute=0, second=0, microsecond=0)
    return nxt


class KiteGateway:
    demo = False

    def __init__(self, store: Store):
        from kiteconnect import KiteConnect  # imported lazily so demo mode needs no key

        self.store = store
        self.kite = KiteConnect(api_key=settings.kite_api_key)
        self.kite.set_session_expiry_hook(self._on_expiry)
        self._instruments: dict[str, list[dict]] = {}
        self._last_hist_call = 0.0
        sess = store.get("kite_session")
        if sess:
            self.kite.set_access_token(sess["access_token"])

    # -- session -----------------------------------------------------------
    def _on_expiry(self) -> None:
        log.warning("Kite session expired")
        self.store.set("kite_session", None)

    def session(self) -> dict | None:
        sess = self.store.get("kite_session")
        if not sess:
            return None
        if now_ist() >= datetime.fromisoformat(sess["valid_until"]):
            self.store.set("kite_session", None)
            return None
        return sess

    def is_ready(self) -> bool:
        return self.session() is not None

    def login_url(self, state: str) -> str:
        # redirect_params is echoed back by Kite to the redirect URL; we use it as a CSRF nonce.
        return self.kite.login_url() + f"&redirect_params=state%3D{state}"

    def complete_login(self, request_token: str) -> dict:
        data = self.kite.generate_session(request_token, api_secret=settings.kite_api_secret)
        self.kite.set_access_token(data["access_token"])
        issued = now_ist()
        sess = {
            "access_token": data["access_token"],
            "user_id": data.get("user_id"),
            "user_name": data.get("user_name"),
            "issued": issued.isoformat(timespec="seconds"),
            "valid_until": _session_valid_until(issued).isoformat(timespec="seconds"),
        }
        self.store.set("kite_session", sess)
        return sess

    def _require(self) -> None:
        if not self.is_ready():
            raise SessionMissing("Kite login needed for today")

    # -- instruments & quotes ----------------------------------------------
    def instruments(self, exchange: str) -> list[dict]:
        self._require()
        cache_key = f"instruments:{exchange}:{date.today().isoformat()}"
        if exchange not in self._instruments or self.store.get("instruments_day:" + exchange) != cache_key:
            self._instruments[exchange] = self.kite.instruments(exchange)
            self.store.set("instruments_day:" + exchange, cache_key)
        return self._instruments[exchange]

    def quotes(self, keys: list[str]) -> dict[str, dict]:
        self._require()
        out: dict[str, dict] = {}
        for i in range(0, len(keys), 450):  # Kite allows up to 500 per call
            out.update(self.kite.quote(keys[i:i + 450]))
        return out

    def daily_candles(self, token: int, days: int = 400) -> pd.DataFrame:
        """Daily OHLCV, cached once per trading day per instrument."""
        self._require()
        today = date.today().isoformat()
        days = min(int(days), 2000)  # Kite returns at most 2000 daily candles per request
        row = self.store.one("SELECT fetched, rows FROM candles WHERE token=? AND interval='day' AND days>=? "
                             "ORDER BY days DESC LIMIT 1", (token, days))
        if row and row["fetched"] == today:
            df = _rows_to_df(row["rows"])
            return df[df.index >= pd.Timestamp(date.today() - timedelta(days=days))]
        wait = 0.35 - (time.time() - self._last_hist_call)  # historical API: ~3 req/s
        if wait > 0:
            time.sleep(wait)
        self._last_hist_call = time.time()
        to_d = datetime.now(IST)
        data = self.kite.historical_data(token, to_d - timedelta(days=days), to_d, "day")
        df = pd.DataFrame(data)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
        df = df.set_index("date")[["open", "high", "low", "close", "volume"]]
        self.store.execute(
            "INSERT OR REPLACE INTO candles(token,interval,days,fetched,rows) VALUES(?,?,?,?,?)",
            (token, "day", days, today, df.reset_index().to_json(orient="records", date_format="iso")),
        )
        return df

    # -- account (used by the live broker) ----------------------------------
    def margins(self) -> dict:
        self._require()
        return self.kite.margins("equity")

    def positions(self) -> dict:
        self._require()
        return self.kite.positions()

    def holdings(self) -> list[dict]:
        self._require()
        return self.kite.holdings()

    def place_order(self, **kw: Any) -> str:
        self._require()
        return self.kite.place_order(**kw)

    def order_history(self, order_id: str) -> list[dict]:
        self._require()
        return self.kite.order_history(order_id)


def _rows_to_df(rows_json: str) -> pd.DataFrame:
    df = pd.read_json(rows_json, orient="records") if rows_json else pd.DataFrame()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df.set_index("date")


# ---------------------------------------------------------------------------
# Demo gateway
# ---------------------------------------------------------------------------
class DemoGateway:
    """Deterministic synthetic market. Never used when DATA_MODE=kite."""

    demo = True

    def __init__(self, store: Store, symbols: list[tuple[str, str]] | None = None):
        self.store = store
        self._symbols = symbols or []
        self._t0 = time.time()
        self._hist_cache: dict[str, pd.DataFrame] = {}

    def set_symbols(self, symbols: list[tuple[str, str]]) -> None:
        self._symbols = symbols

    def session(self) -> dict | None:
        return {"user_id": "DEMO", "user_name": "Demo session", "issued": ts(),
                "valid_until": (now_ist() + timedelta(hours=12)).isoformat(timespec="seconds")}

    def is_ready(self) -> bool:
        return True

    def login_url(self, state: str) -> str:
        return f"{settings.public_url}/kite/callback?request_token=demo&status=success&state={state}"

    def complete_login(self, request_token: str) -> dict:
        return self.session() or {}

    @staticmethod
    def _seed(sym: str) -> int:
        return int(hashlib.sha1(sym.encode()).hexdigest()[:8], 16)

    def _base(self, sym: str) -> float:
        return 100 + (self._seed(sym) % 3900)

    def instruments(self, exchange: str) -> list[dict]:
        out = []
        for i, (sym, _sector) in enumerate(self._symbols):
            out.append({"tradingsymbol": sym, "exchange": exchange, "instrument_token": 1000 + i,
                        "name": sym, "segment": exchange, "instrument_type": "EQ",
                        "lot_size": 1, "tick_size": 0.05})
        out.append({"tradingsymbol": "NIFTY 50", "exchange": "NSE", "instrument_token": NIFTY50_TOKEN,
                    "name": "NIFTY 50", "segment": "INDICES", "instrument_type": "EQ",
                    "lot_size": 0, "tick_size": 0.05})
        return out

    def daily_candles(self, token: int, days: int = 400) -> pd.DataFrame:
        sym = "NIFTY 50" if token == NIFTY50_TOKEN else (
            self._symbols[token - 1000][0] if 0 <= token - 1000 < len(self._symbols) else str(token))
        want = int(min(days, 2000) * 5 / 7)
        cached = self._hist_cache.get(sym)
        if cached is not None:
            return cached.tail(want)
        rng = np.random.default_rng(self._seed(sym))
        n = int(2000 * 5 / 7)  # one fixed history per symbol, sliced to the requested length
        drift = rng.normal(0.0004, 0.0006)
        vol = 0.009 if sym == "NIFTY 50" else rng.uniform(0.012, 0.024)
        rets = rng.normal(drift, vol, n)
        # a few regime changes so signals disagree across names
        for k in rng.integers(0, n, 3):
            rets[k:k + 40] += rng.normal(0, 0.002)
        close = (22000 if sym == "NIFTY 50" else self._base(sym)) * np.exp(np.cumsum(rets))
        idx = pd.bdate_range(end=pd.Timestamp(date.today()) - pd.Timedelta(days=1), periods=n)
        opn = close * (1 + rng.normal(0, vol / 3, n))
        high = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
        low = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
        volume = rng.integers(2e5, 5e6, n) * (1 + np.abs(rets) * 30)
        df = pd.DataFrame({"open": opn, "high": high, "low": low, "close": close,
                           "volume": volume.astype(int)}, index=idx)
        self._hist_cache[sym] = df
        return df.tail(want)

    def quotes(self, keys: list[str]) -> dict[str, dict]:
        out = {}
        minutes = (time.time() - self._t0) / 60
        for key in keys:
            sym = key.split(":", 1)[1]
            token = NIFTY50_TOKEN if sym == "NIFTY 50" else next(
                (1000 + i for i, (s, _) in enumerate(self._symbols) if s == sym), None)
            if token is None:
                continue
            hist = self.daily_candles(token, 400)
            prev = float(hist["close"].iloc[-1])
            rng = random.Random(self._seed(sym) + int(minutes))
            wiggle = math.sin(minutes / 7 + self._seed(sym) % 10) * 0.006 + rng.gauss(0, 0.002)
            lp = round(prev * (1 + wiggle), 2)
            out[key] = {"last_price": lp, "volume": int(hist["volume"].iloc[-20:].mean() * rng.uniform(0.4, 1.6)),
                        "ohlc": {"open": prev, "high": max(prev, lp) * 1.002, "low": min(prev, lp) * 0.998,
                                 "close": prev},
                        "net_change": lp - prev, "timestamp": ts()}
        return out

    # live-broker methods are deliberately unavailable in demo
    def margins(self) -> dict:
        raise RuntimeError("No account in demo mode")

    positions = holdings = margins

    def place_order(self, **kw: Any) -> str:
        raise RuntimeError("Live orders are impossible in demo mode")

    def order_history(self, order_id: str) -> list[dict]:
        raise RuntimeError("No account in demo mode")


def make_gateway(store: Store):
    if settings.is_demo:
        return DemoGateway(store)
    if not settings.kite_api_key or not settings.kite_api_secret:
        raise RuntimeError("Set KITE_API_KEY and KITE_API_SECRET, or DATA_MODE=demo to try the app.")
    return KiteGateway(store)
