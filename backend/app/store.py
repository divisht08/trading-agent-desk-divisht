"""SQLite persistence. One file, one connection, guarded by a lock."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, finished TEXT,
  status TEXT, trigger TEXT, book TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id INTEGER,
  stage TEXT, status TEXT, message TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS signals (
  symbol TEXT, exchange TEXT, sector TEXT, ltp REAL, change_pct REAL,
  score REAL, rule_score REAL, ml_prob REAL, stance TEXT, features TEXT, run_id INTEGER, ts TEXT,
  PRIMARY KEY (symbol, exchange));
CREATE TABLE IF NOT EXISTS ideas (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, ts TEXT, book TEXT,
  symbol TEXT, exchange TEXT, sector TEXT, action TEXT, qty INTEGER,
  price REAL, conviction REAL, size_pct REAL, rationale TEXT, debate TEXT,
  engine TEXT, status TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, book TEXT, idea_id INTEGER,
  symbol TEXT, exchange TEXT, side TEXT, qty INTEGER, product TEXT,
  ref_price REAL, fill_price REAL, charges REAL, status TEXT,
  kite_order_id TEXT, note TEXT, pnl REAL);
CREATE TABLE IF NOT EXISTS positions (
  book TEXT, symbol TEXT, exchange TEXT, sector TEXT, qty INTEGER, avg_price REAL,
  product TEXT, opened TEXT, stop REAL, target REAL, open_fees REAL DEFAULT 0,
  PRIMARY KEY (book, symbol, exchange));
CREATE TABLE IF NOT EXISTS equity (
  book TEXT, ts TEXT, cash REAL, invested REAL, equity REAL, realized REAL,
  PRIMARY KEY (book, ts));
CREATE TABLE IF NOT EXISTS backtests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, params TEXT,
  metrics TEXT, curve TEXT, trades TEXT);
CREATE TABLE IF NOT EXISTS candles (
  token INTEGER, interval TEXT, days INTEGER, fetched TEXT, rows TEXT,
  PRIMARY KEY (token, interval, days));
"""


def now_ist() -> datetime:
    return datetime.now(IST)


def ts() -> str:
    return now_ist().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    # -- generic helpers ---------------------------------------------------
    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.db.execute(sql, params)
            self.db.commit()
            return cur

    def query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # -- key/value ---------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    # -- events (the live flow) --------------------------------------------
    def event(self, run_id: int | None, stage: str, status: str, message: str, data: Any = None) -> int:
        cur = self.execute(
            "INSERT INTO events(ts,run_id,stage,status,message,data) VALUES(?,?,?,?,?,?)",
            (ts(), run_id, stage, status, message, json.dumps(data) if data is not None else None),
        )
        return int(cur.lastrowid)

    def events_since(self, last_id: int, limit: int = 200) -> list[dict]:
        rows = self.query("SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?", (last_id, limit))
        for r in rows:
            r["data"] = json.loads(r["data"]) if r["data"] else None
        return rows

    def recent_events(self, limit: int = 80) -> list[dict]:
        rows = self.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["data"] = json.loads(r["data"]) if r["data"] else None
        return list(reversed(rows))
