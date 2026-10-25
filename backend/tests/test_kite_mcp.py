"""End-to-end test of the free Kite MCP data path against a local fake MCP server.

The fake answers with the same shapes Zerodha's hosted server returned in a live check
(get_profile, get_quotes, get_historical_data, login), and refuses data until "logged in".
"""
import json
import socket
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

STATE = {"logged_in": False}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _fake_server(port: int):
    import uvicorn
    from mcp.server.fastmcp import FastMCP

    m = FastMCP("kite-fake")

    def need_login():
        if not STATE["logged_in"]:
            raise RuntimeError("Please log in first using the login tool")

    @m.tool()
    def login() -> str:
        return ("⚠️ WARNING ... provide the user with this login link: [Login to Kite]"
                "(https://mcp.kite.trade/authorize?session_id=kitemcp-test%7C123.abc%3D)")

    @m.tool()
    def get_profile() -> str:
        need_login()
        return json.dumps({"user_id": "AB1234", "user_name": "Test User", "exchanges": ["NSE", "BSE"]})

    @m.tool()
    def get_quotes(instruments: list[str]) -> str:
        need_login()
        out = {}
        for i, k in enumerate(instruments):
            tok = 256265 if k == "NSE:NIFTY 50" else 1000 + abs(hash(k)) % 90000
            px = 22600.0 if k == "NSE:NIFTY 50" else 100.0 + (abs(hash(k)) % 3000)
            out[k] = {"instrument_token": tok, "timestamp": "2026-10-23T17:34:40+05:30", "last_price": px,
                      "volume": 1000000, "ohlc": {"open": px, "high": px * 1.01, "low": px * 0.99, "close": px * 0.995},
                      "net_change": 0}
        return json.dumps(out)

    @m.tool()
    def get_historical_data(instrument_token: int, from_date: str, to_date: str, interval: str,
                            continuous: bool = False, oi: bool = False) -> str:
        need_login()
        rng = np.random.default_rng(instrument_token)
        idx = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=320)
        c = 500 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, len(idx))))
        rows = [{"date": d.strftime("%Y-%m-%dT00:00:00+05:30"), "open": float(x * 0.998), "high": float(x * 1.01),
                 "low": float(x * 0.99), "close": float(x), "volume": int(rng.integers(1e5, 1e6)), "oi": 0}
                for d, x in zip(idx, c)]
        return json.dumps(rows)

    server = uvicorn.Server(uvicorn.Config(m.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return server
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("fake MCP server did not start")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    port = _free_port()
    server = _fake_server(port)
    STATE["logged_in"] = False
    monkeypatch.setenv("DATA_MODE", "kite_mcp")
    monkeypatch.setenv("KITE_MCP_URL", f"http://127.0.0.1:{port}/mcp")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("KITE_API_KEY", "")
    monkeypatch.setenv("UNIVERSE_INDEX", "nifty50")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path / "nodist"))
    for m in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[m]
    import app.main as main
    yield main
    server.should_exit = True


def test_mcp_login_then_full_cycle(env):
    main = env
    eng = main.engine
    for _ in range(50):
        if eng.gw.client.connected:
            break
        time.sleep(0.1)
    assert eng.gw.client.connected
    # before login: no session, the cycle stops at the first stage and asks for login
    assert not eng.gw.is_ready()
    run = eng.run_cycle("t")
    assert main.store.one("SELECT status FROM runs WHERE id=?", (run,))["status"] == "needs_login"
    assert eng.new_login_link().startswith("https://mcp.kite.trade/authorize?session_id=")
    # "tap the link"
    STATE["logged_in"] = True
    eng.gw._profile = (0.0, -1, None)
    assert eng.gw.session()["user_id"] == "AB1234"
    run = eng.run_cycle("t")
    status = main.store.one("SELECT status FROM runs WHERE id=?", (run,))["status"]
    msgs = [r["message"] for r in main.store.query("SELECT message FROM events WHERE run_id=?", (run,))]
    assert status == "ok", msgs
    assert main.store.one("SELECT COUNT(*) n FROM signals")["n"] >= 40
    assert any("Test mode" in m for m in msgs)


def test_automatic_needs_order_api_key_with_free_data(env):
    eng = env.engine
    assert eng.orders_gw is None
    ok, why = eng.live.preflight()
    assert not ok and "Personal plan" in why


def test_spend_cap_falls_back_to_rules():
    from deskml.agents import Candidate, DecisionDesk, DeskConfig, Spend

    store = {"v": 0.0}

    class Fake:
        def create(self, **kw):
            usage = SimpleNamespace(input_tokens=200_000, output_tokens=10_000, server_tool_use=None)
            if kw.get("tool_choice"):
                return SimpleNamespace(usage=usage, content=[SimpleNamespace(type="tool_use", input={
                    "action": "AVOID", "conviction": 0.4, "size_pct": 0, "horizon": "days", "rationale": "x"})])
            return SimpleNamespace(usage=usage, content=[SimpleNamespace(type="text", text="r")])

    spend = Spend(0.5, lambda: store["v"], lambda v: store.__setitem__("v", v))
    desk = DecisionDesk(DeskConfig(use_web_news=False), spend)
    desk.client = SimpleNamespace(messages=Fake())
    c = Candidate("ITC", "NSE", "FMCG", 400.0, 55, "bullish", ["x"], {"atr_pct": 2})
    first = desk.decide(c, lambda *a: None)
    assert first.engine == "claude" and store["v"] > 0.5        # one debate blew the tiny cap
    second = desk.decide(c, lambda *a: None)
    assert second.engine.startswith("rules (Claude budget")


def test_debate_reuse(env, monkeypatch):
    from deskml.agents import Candidate, Decision
    eng = env.engine
    monkeypatch.setattr(type(eng.desk), "engine", property(lambda self: "claude"))
    c = Candidate("ITC", "NSE", "FMCG", 400.0, 55, "bullish", ["x"], {})
    eng._remember_decision("test", c, Decision("BUY", 0.7, 8, "weeks", "why", engine="claude"))
    again = eng._cached_decision("test", c)
    assert again is not None and again.engine == "claude (reused)"
    c.score = 75                                               # moved by 20 points: debate again
    assert eng._cached_decision("test", c) is None
