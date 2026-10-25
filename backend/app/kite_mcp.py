"""Free market data through Zerodha's hosted Kite MCP (https://mcp.kite.trade/mcp).

The same connector Claude chat uses. Login is per MCP session: the server keeps one
session open, asks the `login` tool for a link, and you approve it on your phone once a
day. If the connection drops, a new session starts and needs a new login.

Used for: profile, quotes, daily history (and instrument tokens via quotes).
Not used for orders: those go through the free Kite Connect Personal API (market.KiteGateway).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta

import pandas as pd

from .config import settings
from .market import IST, SessionMissing, _session_valid_until, cached_daily
from .store import Store, now_ist

log = logging.getLogger(__name__)

LOGIN_URL_RE = re.compile(r"https://mcp\.kite\.trade/authorize\?[^\s)\]>\"']+")
AUTH_ERR_RE = re.compile(r"log ?in|not logged|session|unauthori[sz]ed|access token|authenticat", re.I)


class McpToolError(RuntimeError):
    pass


class McpClient:
    """One long-lived MCP session on a background event loop, called synchronously."""

    def __init__(self, url: str):
        self.url = url
        self.generation = 0          # bumps on every new session (= login needed again)
        self.connected = False
        self.loop = asyncio.new_event_loop()
        self._queue: asyncio.Queue | None = None
        self._ready = threading.Event()
        threading.Thread(target=self._thread, name="kite-mcp", daemon=True).start()
        self._ready.wait(5)

    def _thread(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._queue = asyncio.Queue()
        self._ready.set()
        self.loop.run_until_complete(self._runner())

    async def _runner(self) -> None:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        backoff = 2
        while True:
            try:
                async with streamablehttp_client(self.url, timeout=30) as (read, write, _sid):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        self.connected, backoff = True, 2
                        self.generation += 1
                        log.info("Kite MCP connected (session %s)", self.generation)
                        while True:
                            name, args, fut = await self._queue.get()
                            if name == "__reset__":
                                fut.set_result(None)
                                break
                            try:
                                res = await asyncio.wait_for(session.call_tool(name, args), 90)
                                text = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
                                fut.set_result((bool(res.isError), text))
                            except Exception as e:  # noqa: BLE001 - transport problem: reconnect
                                fut.set_exception(e)
                                raise
            except Exception as e:  # noqa: BLE001
                log.warning("Kite MCP connection lost: %s", e)
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    def call(self, name: str, args: dict | None = None, timeout: float = 100) -> str:
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self.loop.call_soon_threadsafe(self._queue.put_nowait, (name, args or {}, fut))
        is_err, text = fut.result(timeout)
        if is_err:
            raise McpToolError(text)
        return text

    def reset(self) -> None:
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self.loop.call_soon_threadsafe(self._queue.put_nowait, ("__reset__", {}, fut))
        try:
            fut.result(10)
        except Exception:  # noqa: BLE001
            pass


class KiteMcpGateway:
    demo = False
    via = "Kite MCP (free)"

    def __init__(self, store: Store, client: McpClient | None = None):
        self.store = store
        self.client = client or McpClient(settings.kite_mcp_url)
        self._profile: tuple[float, int, dict | None] = (0.0, -1, None)
        self._tokens: dict[str, dict] = {}
        self._tokens_day = ""
        self._last_hist = 0.0

    # -- calls -----------------------------------------------------------------
    def _json(self, tool: str, args: dict | None = None):
        try:
            text = self.client.call(tool, args)
        except McpToolError as e:
            if AUTH_ERR_RE.search(str(e)):
                self._profile = (0.0, -1, None)
                raise SessionMissing("Kite MCP login needed") from e
            raise
        except concurrent.futures.TimeoutError as e:
            raise RuntimeError(f"Kite MCP did not answer {tool} in time") from e
        t = text.strip()
        if not t.startswith(("{", "[")):
            if AUTH_ERR_RE.search(t):
                self._profile = (0.0, -1, None)
                raise SessionMissing("Kite MCP login needed")
            raise RuntimeError(f"Unexpected reply from Kite MCP {tool}: {t[:160]}")
        return json.loads(t)

    # -- session -----------------------------------------------------------------
    def session(self) -> dict | None:
        checked, gen, prof = self._profile
        if gen == self.client.generation and time.time() - checked < 120:
            return prof
        prof = None
        if self.client.connected:
            try:
                p = self._json("get_profile")
                prof = {"user_id": p.get("user_id"), "user_name": p.get("user_name"), "via": self.via,
                        "valid_until": _session_valid_until(now_ist()).isoformat(timespec="seconds")}
            except SessionMissing:
                prof = None
            except Exception as e:  # noqa: BLE001
                log.warning("Kite MCP profile check failed: %s", e)
        self._profile = (time.time(), self.client.generation, prof)
        return prof

    def is_ready(self) -> bool:
        return self.session() is not None

    def login_url(self, state: str = "") -> str:
        text = self.client.call("login")
        m = LOGIN_URL_RE.search(text)
        if not m:
            raise RuntimeError("Kite MCP did not return a login link")
        self._profile = (0.0, -1, None)
        return m.group(0)

    def complete_login(self, request_token: str) -> dict:
        raise RuntimeError("Kite MCP logins finish on Kite's own page; nothing to complete here")

    def _require(self) -> None:
        if not self.is_ready():
            raise SessionMissing("Kite MCP login needed for today")

    # -- instruments & quotes ------------------------------------------------------
    def resolve(self, symbols: list[str], exchange: str) -> list[dict]:
        """Instrument records for symbols, using the tokens that quotes return."""
        self._require()
        today = date.today().isoformat()
        if self._tokens_day != today:
            self._tokens, self._tokens_day = {}, today
        missing = [f"{exchange}:{s}" for s in symbols if f"{exchange}:{s}" not in self._tokens]
        if missing:
            for k, q in self.quotes(missing).items():
                sym = k.split(":", 1)[1]
                self._tokens[k] = {"tradingsymbol": sym, "exchange": exchange, "segment": exchange,
                                   "instrument_type": "EQ", "instrument_token": int(q["instrument_token"]),
                                   "name": sym}
        return [self._tokens[f"{exchange}:{s}"] for s in symbols if f"{exchange}:{s}" in self._tokens]

    def instruments(self, exchange: str) -> list[dict]:
        raise NotImplementedError("Kite MCP has no full instrument dump; the universe uses resolve()")

    def quotes(self, keys: list[str]) -> dict[str, dict]:
        self._require()
        out: dict[str, dict] = {}
        for i in range(0, len(keys), 450):
            out.update(self._json("get_quotes", {"instruments": keys[i:i + 450]}) or {})
        return out

    def daily_candles(self, token: int, days: int = 400) -> pd.DataFrame:
        self._require()

        def fetch(days_: int) -> list[dict]:
            wait = 0.35 - (time.time() - self._last_hist)
            if wait > 0:
                time.sleep(wait)
            self._last_hist = time.time()
            to_d = datetime.now(IST)
            fmt = "%Y-%m-%d %H:%M:%S"
            return self._json("get_historical_data", {
                "instrument_token": token, "interval": "day",
                "from_date": (to_d - timedelta(days=days_)).strftime(fmt), "to_date": to_d.strftime(fmt)}) or []

        return cached_daily(self.store, token, days, fetch)

    # -- account: orders never go through the MCP ------------------------------------
    def _no_orders(self, *a, **k):
        raise RuntimeError("Orders go through the Kite Connect Personal API, not the MCP")

    margins = positions = holdings = place_order = order_history = _no_orders
