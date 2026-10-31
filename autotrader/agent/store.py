"""SQLite persistence: positions, trades, daily PnL, events, auth token."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions(
  symbol TEXT PRIMARY KEY, product TEXT, qty INTEGER, entry REAL, stop REAL,
  target REAL, strategy TEXT, opened_at TEXT, peak REAL);
CREATE TABLE IF NOT EXISTS trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, side TEXT, qty INTEGER,
  price REAL, product TEXT, strategy TEXT, pnl REAL, reason TEXT, mode TEXT);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, level TEXT, msg TEXT);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("AGENT_DB", "agent.db")
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def _x(self, sql: str, args: tuple = ()):
        with self._lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    # key/value
    def set(self, k: str, v: Any) -> None:
        self._x("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, json.dumps(v)))

    def get(self, k: str, default: Any = None) -> Any:
        r = self._x("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    # events
    def log(self, msg: str, level: str = "INFO") -> None:
        print(f"[{datetime.now():%H:%M:%S}] {level} {msg}", flush=True)
        self._x("INSERT INTO events(ts,level,msg) VALUES(?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), level, msg))

    def events(self, n: int = 50) -> list[dict]:
        return [dict(r) for r in self._x(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (n,)).fetchall()]

    # positions
    def positions(self) -> dict[str, dict]:
        return {r["symbol"]: dict(r) for r in self._x("SELECT * FROM positions").fetchall()}

    def upsert_position(self, p: dict) -> None:
        self._x(
            "INSERT OR REPLACE INTO positions VALUES(?,?,?,?,?,?,?,?,?)",
            (p["symbol"], p["product"], p["qty"], p["entry"], p["stop"], p["target"],
             p["strategy"], p["opened_at"], p.get("peak", p["entry"])))

    def delete_position(self, symbol: str) -> None:
        self._x("DELETE FROM positions WHERE symbol=?", (symbol,))

    # trades
    def add_trade(self, t: dict) -> None:
        self._x(
            "INSERT INTO trades(ts,symbol,side,qty,price,product,strategy,pnl,reason,mode)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (t["ts"], t["symbol"], t["side"], t["qty"], t["price"], t["product"],
             t["strategy"], t.get("pnl", 0.0), t.get("reason", ""), t["mode"]))

    def trades(self, since: str | None = None) -> list[dict]:
        if since:
            rows = self._x("SELECT * FROM trades WHERE ts>=? ORDER BY id", (since,))
        else:
            rows = self._x("SELECT * FROM trades ORDER BY id")
        return [dict(r) for r in rows.fetchall()]

    def realized_pnl(self, since: str) -> float:
        r = self._x("SELECT COALESCE(SUM(pnl),0) s FROM trades WHERE ts>=?", (since,)).fetchone()
        return float(r["s"])
