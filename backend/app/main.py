"""HTTP API, Kite login callback, live event stream, and the built React app."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from deskml.backtest import ML_STRATEGIES, STRATEGIES, BTParams, default_dates, run_backtest

from . import scheduler
from .config import settings
from .engine import MODES, STAGE_LABELS, STAGES, Engine
from .market import SessionMissing
from .security import password_ok, session_token
from .store import Store, ts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

store = Store(settings.db_path)
engine = Engine(store)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    sched = scheduler.start(engine) if settings.scheduler_enabled else None
    if not settings.dashboard_password:
        log.warning("DASHBOARD_PASSWORD is empty - anyone who can reach this URL can control the agent, "
                    "and Automatic mode cannot be switched on.")
    yield
    if sched:
        sched.shutdown(wait=False)


app = FastAPI(title="Agent Desk", lifespan=lifespan)
OPEN_PREFIXES = ("/api/auth/", "/kite/callback", "/healthz")


def _authed(request: Request) -> bool:
    return not settings.dashboard_password or hmac.compare_digest(request.cookies.get("kad", ""), session_token())


@app.middleware("http")
async def auth(request: Request, call_next):
    p = request.url.path
    if p.startswith("/api/") and not p.startswith(OPEN_PREFIXES) and not _authed(request):
        return JSONResponse({"detail": "Sign in first"}, status_code=401)
    return await call_next(request)


def _err(e: Exception, code: int = 403):
    raise HTTPException(code, str(e))


# ---------------------------------------------------------------- auth
@app.get("/api/auth/me")
def me(request: Request):
    return {"authenticated": _authed(request), "password_required": bool(settings.dashboard_password)}


@app.post("/api/auth/login")
def login(body: dict = Body(...)):
    if not password_ok(body.get("password", "")):
        raise HTTPException(401, "That password didn't match")
    r = JSONResponse({"ok": True})
    r.set_cookie("kad", session_token(), httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30,
                 secure=settings.public_url.startswith("https"))
    return r


@app.post("/api/auth/logout")
def logout():
    r = JSONResponse({"ok": True})
    r.delete_cookie("kad")
    return r


@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": ts()}


# ---------------------------------------------------------------- Kite login (works from the phone)
CALLBACK_HTML = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><title>{t}</title>
<body style="font:16px system-ui;display:grid;place-items:center;min-height:90vh;margin:0;background:#EEF1F4;color:#16202A">
<div style="background:#fff;border:1px solid #D8DEE5;border-radius:14px;padding:28px;max-width:360px">
<h1 style="font-size:1.3rem;margin:0 0 8px">{t}</h1><p style="margin:0 0 16px;color:#4E5B68">{m}</p>
<a href="/" style="display:inline-block;background:#3D4FD6;color:#fff;padding:8px 16px;border-radius:999px;text-decoration:none">Open the desk</a></div>"""


@app.get("/kite/callback", response_class=HTMLResponse)
def kite_callback(request_token: str = "", status: str = "", state: str = ""):
    saved = store.get("login_state") or {}
    if status != "success" or not request_token:
        return HTMLResponse(CALLBACK_HTML.format(t="Login not completed", m="Kite did not return a token. Try again."), 400)
    if not state or state != saved.get("state"):
        return HTMLResponse(CALLBACK_HTML.format(t="Link expired", m="This login link is old. Send a new one from the desk."), 400)
    try:
        sess = engine.gw.complete_login(request_token)
    except Exception as e:  # noqa: BLE001
        return HTMLResponse(CALLBACK_HTML.format(t="Login failed", m=f"Kite said: {e}"), 400)
    store.set("login_state", None)
    store.event(None, "session", "done", f"Kite login completed for {sess.get('user_id')}")
    threading.Thread(target=engine.run_cycle, args=("after-login",), daemon=True).start()
    return HTMLResponse(CALLBACK_HTML.format(
        t="You're logged in", m=f"Kite session active for {sess.get('user_name') or sess.get('user_id')}. "
                                "The agent is running."))


@app.get("/api/login-link")
def login_link():
    return {"url": engine.new_login_link()}


@app.post("/api/login/push")
def login_push():
    return {"sent": engine.send_login_to_phone("Login requested from the dashboard")}


# ---------------------------------------------------------------- read APIs
@app.get("/api/status")
def status():
    s = engine.status()
    s["password_set"] = bool(settings.dashboard_password)
    s["phone_channels"] = [c for c, ok in (("ntfy", settings.ntfy_topic), ("telegram", settings.telegram_bot_token)) if ok]
    s["stages"] = [{"id": k, "label": STAGE_LABELS[k]} for k in STAGES]
    return s


@app.get("/api/flow")
def flow():
    run = store.one("SELECT * FROM runs WHERE trigger NOT IN ('square-off','flatten') ORDER BY id DESC LIMIT 1")
    if not run:
        return {"run": None, "stages": {}}
    stages: dict[str, dict] = {}
    for r in store.query("SELECT stage,status,message,ts FROM events WHERE run_id=? ORDER BY id", (run["id"],)):
        if r["stage"] in STAGE_LABELS:
            stages[r["stage"]] = r
    run["summary"] = json.loads(run["summary"]) if run.get("summary") else {}
    return {"run": run, "stages": stages}


def _book(book: str | None) -> str:
    b = book or engine.book
    if b not in ("test", "live"):
        raise HTTPException(400, "book must be test or live")
    return b


@app.get("/api/overview")
def overview(book: str | None = None):
    b = _book(book)
    led = engine.ledgers[b]
    positions, invested = [], 0.0
    for p in led.positions():
        sig = store.one("SELECT ltp, change_pct, score, stance FROM signals WHERE symbol=? AND exchange=?",
                        (p["symbol"], p["exchange"]))
        ltp = sig["ltp"] if sig else p["avg_price"]
        invested += p["qty"] * ltp
        positions.append({**p, "ltp": ltp, "value": round(p["qty"] * ltp, 2),
                          "pnl": round((ltp - p["avg_price"]) * p["qty"], 2),
                          "pnl_pct": round((ltp / p["avg_price"] - 1) * 100 * (1 if p["qty"] > 0 else -1), 2),
                          "day_change_pct": sig["change_pct"] if sig else None, "stance": sig["stance"] if sig else None})
    cash, start = led.cash, led.start_capital
    equity = cash + invested
    day_start = store.get(f"day_start_equity:{b}:{date.today().isoformat()}")
    return {"book": b, "cash": round(cash, 2), "invested": round(invested, 2), "equity": round(equity, 2),
            "start_capital": start, "total_pnl": round(equity - start, 2),
            "total_pnl_pct": round((equity / start - 1) * 100, 2) if start else 0,
            "day_pnl": round(equity - day_start, 2) if day_start else None, "realized": led.realized,
            "positions": positions,
            "curve": store.query("SELECT ts, equity FROM equity WHERE book=? ORDER BY ts DESC LIMIT 500", (b,))[::-1]}


@app.get("/api/signals")
def signals():
    rows = store.query("SELECT * FROM signals ORDER BY score DESC")
    sectors: dict[str, dict] = {}
    for r in rows:
        f = json.loads(r.pop("features") or "{}")
        r.update(reasons=f.get("reasons", []), spark=f.get("spark", []), rsi=f.get("rsi14"),
                 turnover_cr=f.get("turnover_cr"))
        s = sectors.setdefault(r["sector"], {"sector": r["sector"], "n": 0, "bullish": 0, "bearish": 0, "neutral": 0,
                                             "score_sum": 0.0, "chg_sum": 0.0})
        s["n"] += 1
        s[r["stance"]] += 1
        s["score_sum"] += r["score"]
        s["chg_sum"] += r["change_pct"] or 0
    sec = [{"sector": s["sector"], "n": s["n"], "bullish": s["bullish"], "bearish": s["bearish"],
            "avg_score": round(s["score_sum"] / s["n"], 1), "avg_change": round(s["chg_sum"] / s["n"], 2)}
           for s in sectors.values()]
    sec.sort(key=lambda x: -x["avg_score"])
    return {"stocks": rows, "sectors": sec, "as_of": rows[0]["ts"] if rows else None}


@app.get("/api/ideas")
def ideas(limit: int = 100, book: str | None = None):
    rows = store.query("SELECT * FROM ideas WHERE book=? ORDER BY id DESC LIMIT ?", (_book(book), limit))
    for r in rows:
        r["debate"] = json.loads(r["debate"] or "{}")
    return rows


@app.get("/api/orders")
def orders(limit: int = 100, book: str | None = None):
    return store.query("SELECT * FROM orders WHERE book=? ORDER BY id DESC LIMIT ?", (_book(book), limit))


@app.get("/api/events")
def events(after: int = 0, limit: int = 120):
    return store.events_since(after, limit) if after else store.recent_events(limit)


@app.get("/api/stream")
async def stream(request: Request, after: int = 0):
    async def gen():
        last = after
        if not last:
            row = store.one("SELECT MAX(id) m FROM events")
            last = (row["m"] or 0) if row else 0
        while not await request.is_disconnected():
            for ev in store.events_since(last):
                last = ev["id"]
                yield f"id: {ev['id']}\nevent: flow\ndata: {json.dumps(ev)}\n\n"
            yield ": keepalive\n\n"
            await asyncio.sleep(1.0)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- control
@app.post("/api/run")
def run_now():
    if engine.running:
        return {"started": False, "note": "A cycle is already running"}
    threading.Thread(target=engine.run_cycle, args=("manual",), daemon=True).start()
    return {"started": True}


@app.post("/api/mode")
def set_mode(body: dict = Body(...)):
    mode = body.get("mode")
    if mode not in MODES:
        raise HTTPException(400, f"mode must be one of {list(MODES)}")
    if mode == "test":
        engine.set_test_mode()
        return engine.status()
    try:
        return engine.arm_automatic(body.get("password", ""), body.get("code", ""))
    except PermissionError as e:
        _err(e)


@app.post("/api/halt")
def halt(body: dict = Body(...)):
    halted = bool(body.get("halted"))
    store.set("halted", halted)
    store.event(None, "session", "blocked" if halted else "done",
                "Stop switch ON - agent stopped" if halted else "Stop switch OFF - agent resumed")
    closed = engine.flatten_all() if halted and body.get("flatten") else 0
    return {**engine.status(), "flattened": closed}


@app.post("/api/test/reset")
def reset_test(body: dict = Body(default={})):
    cap = float(body.get("capital") or settings.paper_capital)
    engine.ledgers["test"].reset(cap)
    store.event(None, "reconcile", "done", f"Test book reset to ₹{cap:,.0f}")
    return {"ok": True}


@app.post("/api/live/fix-position")
def fix_position(body: dict = Body(...)):
    pos = engine.ledgers["live"].position(body.get("symbol", ""), body.get("exchange", "NSE"))
    qty = int(body.get("qty", -1))
    if not pos:
        raise HTTPException(404, "No such live position")
    if qty < 0 or qty > pos["qty"]:
        raise HTTPException(400, "You can only lower the agent's quantity, never raise it")
    engine.fix_live_position(pos["symbol"], pos["exchange"], qty)
    return {"ok": True}


# ---------------------------------------------------------------- two-step verification
@app.post("/api/security/totp/setup")
def totp_setup(body: dict = Body(...)):
    try:
        return engine.twostep.start_setup(body.get("password", ""))
    except PermissionError as e:
        _err(e)


@app.post("/api/security/totp/confirm")
def totp_confirm(body: dict = Body(...)):
    try:
        engine.twostep.confirm_setup(body.get("code", ""))
        return {"ok": True}
    except PermissionError as e:
        _err(e)


@app.post("/api/security/totp/remove")
def totp_remove(body: dict = Body(...)):
    try:
        engine.twostep.remove(body.get("password", ""), body.get("code", ""))
        engine.set_test_mode()
        return {"ok": True}
    except PermissionError as e:
        _err(e)


# ---------------------------------------------------------------- models
@app.get("/api/models")
def models():
    return {"models": engine.registry.list(), "ml_weight": settings.ml_weight, "min_auc": settings.ml_min_auc,
            "training": engine._train_lock.locked()}


@app.post("/api/models/train")
def models_train():
    if engine._train_lock.locked():
        return {"started": False, "note": "Training is already running"}
    threading.Thread(target=engine.train_model, args=("manual",), daemon=True).start()
    return {"started": True}


@app.post("/api/models/{version}/activate")
def models_activate(version: str):
    try:
        engine.registry.activate(version)
    except FileNotFoundError:
        raise HTTPException(404, "No such model")
    store.event(None, "model", "done", f"Model {version} activated by you")
    return {"ok": True}


@app.post("/api/models/deactivate")
def models_deactivate():
    engine.registry.deactivate()
    store.event(None, "model", "done", "Model switched off - rules only")
    return {"ok": True}


# ---------------------------------------------------------------- backtesting
@app.get("/api/backtest/strategies")
def bt_strategies():
    s, e = default_dates()
    return {"strategies": STRATEGIES, "ml_strategies": sorted(ML_STRATEGIES), "default_start": s, "default_end": e}


@app.post("/api/backtest")
def backtest(body: dict = Body(...)):
    p = BTParams(**{k: v for k, v in body.items() if k in BTParams.__dataclass_fields__ and v not in ("", None)})
    if not p.start or not p.end:
        p.start, p.end = default_dates()
    try:
        uni = engine._universe_now()
    except SessionMissing:
        raise HTTPException(409, "Log in to Kite first - backtests use Kite historical data")
    symbols = [s.strip().upper() for s in str(body.get("symbols", "")).split(",") if s.strip()]
    pool = [s for s in uni if s.symbol in symbols] if symbols else uni[: int(body.get("limit", 40))]
    if not pool:
        raise HTTPException(400, "None of those symbols are in the universe")
    warmup = 420 if p.strategy not in ML_STRATEGIES else 800
    days = min(2000, (date.today() - date.fromisoformat(p.start)).days + warmup)
    try:
        data, exch, bench_df = engine.history_for(pool, days)
    except SessionMissing:
        raise HTTPException(409, "Log in to Kite first - backtests use Kite historical data")
    res = run_backtest(data, bench_df["close"] if len(bench_df) else None, p, exch)
    if "error" in res:
        raise HTTPException(400, res["error"])
    res["params"] = {**p.__dict__, "symbols": [s.symbol for s in pool]}
    res["demo"] = engine.gw.demo
    cur = store.execute("INSERT INTO backtests(ts,params,metrics,curve,trades) VALUES(?,?,?,?,?)",
                        (ts(), json.dumps(res["params"]), json.dumps(res["metrics"]), json.dumps(res["curve"]),
                         json.dumps(res["trades"])))
    res["id"] = cur.lastrowid
    return res


@app.get("/api/backtests")
def backtests():
    rows = store.query("SELECT id, ts, params, metrics FROM backtests ORDER BY id DESC LIMIT 20")
    for r in rows:
        r["params"], r["metrics"] = json.loads(r["params"]), json.loads(r["metrics"])
    return rows


# ---------------------------------------------------------------- the React app
DIST = settings.frontend_dist
if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Not found")
    f = (DIST / path).resolve()
    if path and f.is_file() and DIST.resolve() in f.parents:
        return FileResponse(f)
    index = DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return HTMLResponse("<p>Frontend not built. Run <code>npm run build</code> in frontend/, "
                        "or use <code>npm run dev</code> on port 5173.</p>", 503)
