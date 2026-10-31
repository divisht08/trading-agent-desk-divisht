"""Service entrypoint: FastAPI (login callback + dashboard + kill switch) and the trading thread.

Daily flow: Zerodha tokens expire every morning. From 08:00 IST on weekdays the agent sends you a login link once
(Telegram if configured, otherwise open /login yourself). You approve on your phone, Zerodha
redirects to /callback, the token is stored, and the agent trades for the day with no laptop.
"""
from __future__ import annotations

import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .broker import KiteBroker, KiteData, PaperBroker
from .config import Config
from .engine import Engine
from .store import Store

cfg = Config.load()
store = Store()
app = FastAPI(title="Trading Agent")
DASH_KEY = os.environ.get("DASH_KEY", "")


def _kite():
    from kiteconnect import KiteConnect
    return KiteConnect(api_key=os.environ["KITE_API_KEY"])


def notify(text: str) -> None:
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        store.log(f"NOTIFY: {text}")
        return
    try:
        q = urllib.parse.urlencode({"chat_id": chat, "text": text})
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage?{q}", timeout=10)
    except Exception as e:
        store.log(f"telegram failed: {e}", "WARN")


def _auth(key: str | None) -> None:
    if not DASH_KEY or key != DASH_KEY:
        raise HTTPException(401, "bad key")


@app.get("/login")
def login():
    return RedirectResponse(_kite().login_url())


@app.get("/callback")
def callback(request_token: str = "", status: str = ""):
    if status != "success" or not request_token:
        raise HTTPException(400, "login failed")
    k = _kite()
    sess = k.generate_session(request_token, api_secret=os.environ["KITE_API_SECRET"])
    store.set("kite_token", {"token": sess["access_token"], "day": datetime.now().strftime("%Y-%m-%d")})
    store.log("Kite login OK")
    return HTMLResponse("<h3>Login OK. Agent is trading. You can close this tab.</h3>")


@app.post("/pause")
def pause(key: str = ""):
    _auth(key)
    store.set("paused", True)
    store.log("PAUSED via dashboard")
    return {"paused": True}


@app.post("/resume")
def resume(key: str = ""):
    _auth(key)
    store.set("paused", False)
    store.log("RESUMED via dashboard")
    return {"paused": False}


@app.get("/state")
def state(key: str = ""):
    _auth(key)
    now = datetime.now()
    pos = store.positions()
    return {
        "mode": cfg.mode, "live_enabled": cfg.live_enabled(), "capital": cfg.capital,
        "paused": store.get("paused", False),
        "logged_in_today": (store.get("kite_token") or {}).get("day") == now.strftime("%Y-%m-%d"),
        "day_pnl": store.realized_pnl(now.strftime("%Y-%m-%d")),
        "total_realized_pnl": store.realized_pnl("0000"),
        "positions": list(pos.values()),
        "recent_trades": store.trades()[-30:],
        "events": store.events(30),
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(key: str = ""):
    _auth(key)
    s = state(key)
    rows = "".join(
        f"<tr><td>{p['symbol']}</td><td>{p['product']}</td><td>{p['qty']}</td><td>{p['entry']:.2f}</td>"
        f"<td>{p['stop']:.2f}</td><td>{p['strategy']}</td></tr>" for p in s["positions"])
    ev = "".join(f"<div>{e['ts'][11:]} {e['level']} {e['msg']}</div>" for e in s["events"])
    return f"""<html><body style="font-family:system-ui;max-width:900px;margin:20px auto;padding:0 12px">
<h2>Trading Agent — {s['mode'].upper()} {'(LIVE UNLOCKED)' if s['live_enabled'] else ''}</h2>
<p>Capital ₹{s['capital']:,.0f} · Today ₹{s['day_pnl']:+,.0f} · Total realized ₹{s['total_realized_pnl']:+,.0f}
· Logged in: {s['logged_in_today']} · Paused: {s['paused']}</p>
<h3>Open positions</h3><table border=1 cellpadding=6><tr><th>Symbol</th><th>Product</th><th>Qty</th>
<th>Entry</th><th>Stop</th><th>Strategy</th></tr>{rows or '<tr><td colspan=6>none</td></tr>'}</table>
<h3>Log</h3><div style="font:12px monospace">{ev}</div></body></html>"""


def trading_thread() -> None:
    engine: Engine | None = None
    warned_day = None
    while True:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        tok = store.get("kite_token")
        if not tok or tok["day"] != today:
            engine = None
            if now.weekday() < 5 and now.hour >= 8 and warned_day != today:
                warned_day = today
                base = os.environ.get("PUBLIC_URL", "http://localhost:8000")
                notify(f"Approve today's Kite login: {base}/login")
            time.sleep(30)
            continue
        if engine is None:
            kite = _kite()
            kite.set_access_token(tok["token"])
            if cfg.live_enabled():
                tokens = {i["tradingsymbol"]: i["instrument_token"]
                          for i in kite.instruments("NSE") if i["segment"] == "NSE"}
                broker = KiteBroker(kite, tokens, cfg)
            else:
                broker = PaperBroker(KiteData(kite), cfg["costs"])
            engine = Engine(cfg, broker, store)
            store.log(f"engine ready ({broker.name})")
        try:
            engine.tick()
        except Exception as e:
            store.log(f"tick error: {e}", "ERROR")
            if "token" in str(e).lower() or "TokenException" in type(e).__name__:
                store.set("kite_token", None)
        time.sleep(cfg["market"]["tick_seconds"])


def main() -> None:
    threading.Thread(target=trading_thread, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
