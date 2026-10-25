import sys
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_MODE", "demo")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pw")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path / "nodist"))
    for m in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[m]
    import app.main as main
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"password": "pw"}).status_code == 200
    return main, c


def test_auth_required(env):
    main, _ = env
    anon = TestClient(main.app)
    assert anon.get("/api/status").status_code == 401
    assert anon.get("/api/auth/me").json() == {"authenticated": False, "password_required": True}
    assert anon.post("/api/auth/login", json={"password": "nope"}).status_code == 401


def test_ledger_round_trip(env):
    main, _ = env
    led = main.engine.ledgers["test"]
    cash0 = led.cash
    led.apply("ABC", "NSE", "X", "BUY", 10, 100.0, 1.0, "CNC")
    assert led.position("ABC", "NSE")["qty"] == 10
    assert led.apply("ABC", "NSE", "X", "SELL", 10, 110.0, 1.5, "CNC") == pytest.approx(100 - 2.5)
    assert led.position("ABC", "NSE") is None and led.cash == pytest.approx(cash0 + 97.5)
    assert main.engine.ledgers["live"].positions() == []          # books are separate


def test_test_mode_cycle(env):
    main, c = env
    st = c.get("/api/status").json()
    assert st["mode"] == "test" and st["book"] == "test"
    run = main.engine.run_cycle("t")
    stages = {r["stage"] for r in main.store.query("SELECT DISTINCT stage FROM events WHERE run_id=?", (run,))}
    assert {"session", "universe", "market", "signals", "debate", "risk", "execute", "reconcile"} <= stages
    orders = c.get("/api/orders").json()
    assert orders and all(o["book"] == "test" for o in orders)
    assert c.get("/api/orders?book=live").json() == []
    assert c.get("/api/overview").json()["positions"]


def test_automatic_needs_two_steps_and_server_switch(env):
    main, c = env
    from app.security import totp_now
    # no authenticator yet
    r = c.post("/api/mode", json={"mode": "automatic", "password": "pw", "code": "000000"})
    assert r.status_code == 403
    # set up the authenticator
    assert c.post("/api/security/totp/setup", json={"password": "bad"}).status_code == 403
    secret = c.post("/api/security/totp/setup", json={"password": "pw"}).json()["secret"]
    assert c.post("/api/security/totp/confirm", json={"code": "123456" if totp_now(secret) != "123456" else "654321"}).status_code == 403
    assert c.post("/api/security/totp/confirm", json={"code": totp_now(secret)}).status_code == 200
    assert c.get("/api/status").json()["twostep"]["enabled"]
    # demo data can never go live, even with both steps
    r = c.post("/api/mode", json={"mode": "automatic", "password": "pw", "code": totp_now(secret)})
    assert r.status_code == 403 and "demo" in r.json()["detail"]
    assert c.get("/api/status").json()["mode"] == "test"


def test_twostep_check_rules(env):
    main, _ = env
    from app.security import totp_now
    ts_ = main.engine.twostep
    secret = ts_.start_setup("pw")["secret"]
    ts_.confirm_setup(totp_now(secret))
    with pytest.raises(PermissionError, match="already used"):
        ts_.check("pw", totp_now(secret))          # same code as setup cannot be replayed
    main.store.set("totp_last", -1)
    ts_.check("pw", totp_now(secret))              # good password + fresh code
    main.store.set("totp_last", -1)
    with pytest.raises(PermissionError, match="Password"):
        ts_.check("wrong", totp_now(secret))
    for _ in range(4):
        with pytest.raises(PermissionError):
            ts_.check("wrong", "000000")
    with pytest.raises(PermissionError, match="Too many"):
        ts_.check("pw", totp_now(secret))          # locked after 5 failures


def test_arm_automatic_when_all_gates_pass(env, monkeypatch):
    main, c = env
    from app.security import totp_now
    eng = main.engine
    monkeypatch.setattr(eng.live, "preflight", lambda: (True, ""))
    monkeypatch.setattr(eng, "orders_gw", type("G", (), {"is_ready": lambda self: True, "session": lambda self: {}})())
    monkeypatch.setattr(main.settings, "live_capital", 50000.0)
    secret = eng.twostep.start_setup("pw")["secret"]
    eng.twostep.confirm_setup(totp_now(secret))
    main.store.set("totp_last", -1)
    r = c.post("/api/mode", json={"mode": "automatic", "password": "pw", "code": totp_now(secret)})
    assert r.status_code == 200 and r.json()["mode"] == "automatic" and r.json()["book"] == "live"
    assert eng.ledgers["live"].cash == 50000.0
    assert c.post("/api/mode", json={"mode": "test"}).json()["mode"] == "test"


def test_live_broker_refuses_in_demo(env, monkeypatch):
    main, _ = env
    monkeypatch.setattr(main.settings, "live_trading_enabled", True)
    monkeypatch.setattr(main.settings, "live_capital", 1000.0)
    fill = main.engine.live.execute("ITC", "NSE", "BUY", 1, 400.0, "CNC")
    assert not fill.ok and fill.status == "BLOCKED"


def test_fix_position_only_lowers(env):
    main, c = env
    main.engine.ledgers["live"].apply("ITC", "NSE", "FMCG", "BUY", 10, 400.0, 1.0, "CNC")
    assert c.post("/api/live/fix-position", json={"symbol": "ITC", "exchange": "NSE", "qty": 11}).status_code == 400
    assert c.post("/api/live/fix-position", json={"symbol": "ITC", "exchange": "NSE", "qty": 4}).status_code == 200
    assert main.engine.ledgers["live"].position("ITC", "NSE")["qty"] == 4


def test_halt_blocks(env):
    main, c = env
    c.post("/api/halt", json={"halted": True})
    run = main.engine.run_cycle("t")
    assert main.store.one("SELECT status FROM runs WHERE id=?", (run,))["status"] == "halted"


def test_login_callback_state(env):
    main, c = env
    main.engine.new_login_link()
    assert c.get("/kite/callback", params={"request_token": "x", "status": "success", "state": "bad"}).status_code == 400
    good = main.store.get("login_state")["state"]
    assert c.get("/kite/callback", params={"request_token": "x", "status": "success", "state": good}).status_code == 200


def test_model_training_and_blend(env):
    main, c = env
    out = main.engine.train_model("manual")
    assert out and out["metrics"]["holdout"]["n"] > 100
    models = c.get("/api/models").json()["models"]
    assert len(models) == 1
    # demo prices are random: the model should not clear the activation bar by much, if at all
    v = models[0]["version"]
    assert c.post(f"/api/models/{v}/activate").status_code == 200
    run = main.engine.run_cycle("t")
    sig = c.get("/api/signals").json()["stocks"]
    assert all(s["ml_prob"] is not None for s in sig)
    assert main.store.one("SELECT status FROM runs WHERE id=?", (run,))["status"] == "ok"


def test_backtest_endpoint(env):
    _, c = env
    r = c.post("/api/backtest", json={"strategy": "sma_cross", "limit": 20})
    assert r.status_code == 200 and "metrics" in r.json()
    r = c.post("/api/backtest", json={"strategy": "blend", "limit": 15})
    assert r.status_code == 200 and r.json()["model_signals_from"]
