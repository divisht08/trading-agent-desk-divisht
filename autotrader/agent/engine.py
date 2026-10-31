"""Trading loop. One tick = manage exits, then look for entries."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pandas as pd

from . import research
from .broker import Broker, PaperBroker
from .config import Config
from .risk import RiskManager
from .store import Store
from .strategies import Signal, intraday_orb, swing_exit, swing_trend


def _t(s: str) -> datetime.time:
    return datetime.strptime(s, "%H:%M").time()


def week_start(now: datetime) -> str:
    return (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")


class Engine:
    def __init__(self, cfg: Config, broker: Broker, store: Store):
        import copy
        cfg = Config(copy.deepcopy(cfg.raw))   # validation edits must not leak into the shared config
        self.cfg, self.broker, self.store = cfg, broker, store
        self.risk = RiskManager(cfg["risk"], cfg.capital)
        self.m = cfg["market"]
        self.universe: list[str] = cfg["universe"]
        self.daily_cache: dict[str, tuple[str, pd.DataFrame]] = {}
        self.swing_scan_day: str | None = None
        self.apply_validation()

    def apply_validation(self) -> None:
        """Backtest gate: a strategy trades ONLY if validation.json says it passed out-of-sample,
        and it trades with the validated parameters. No file / no pass => strategy disabled."""
        import json
        import os
        path = os.environ.get("AGENT_VALIDATION", "validation.json")
        try:
            with open(path) as f:
                v = json.load(f)["strategies"]
        except (OSError, ValueError, KeyError):
            v = {}
        for name, st in self.cfg["strategies"].items():
            res = v.get(name)
            if not (res and res.get("passed")):
                if st.get("enabled"):
                    self.store.log(f"{name} DISABLED: no passing backtest in {path}", "WARN")
                st["enabled"] = False
            else:
                st.update(res["best_params"])
                self.store.log(f"{name} enabled with validated params {res['best_params']}")

    # ---------- helpers ----------
    def paused(self) -> bool:
        return bool(self.store.get("paused", False))

    def fee(self, price: float, qty: int) -> float:
        if isinstance(self.broker, PaperBroker):
            return self.broker.fee(price, qty)
        return 0.0  # live: costs are real, PnL reconciled from broker reports

    def cash(self, positions: dict) -> float:
        if hasattr(self.broker, "cash") and self.cfg.live_enabled():
            return min(self.broker.cash(), self.cfg.capital)
        realized = self.store.realized_pnl("0000")
        invested = sum(p["qty"] * p["entry"] for p in positions.values())
        return self.cfg.capital + realized - invested

    def pnl(self, now: datetime) -> tuple[float, float]:
        day = self.store.realized_pnl(now.strftime("%Y-%m-%d"))
        wk = self.store.realized_pnl(week_start(now))
        return day, wk

    def daily(self, symbol: str, now: datetime) -> pd.DataFrame:
        """COMPLETED daily bars only (today's partial bar removed) — same as the backtest sees."""
        key = now.strftime("%Y-%m-%d")
        c = self.daily_cache.get(symbol)
        if c and c[0] == key:
            return c[1]
        df = self.broker.history(symbol, "1d", 420)
        if len(df):
            df = df[df["date"].dt.normalize() < pd.Timestamp(now.date())].reset_index(drop=True)
        self.daily_cache[symbol] = (key, df)
        return df

    def bars5(self, symbol: str, now: datetime) -> pd.DataFrame:
        """COMPLETED 5-minute bars only (the still-forming bar is dropped)."""
        df = self.broker.history(symbol, "5m", 6)
        if len(df):
            df = df[df["date"] + pd.Timedelta(minutes=5) <= pd.Timestamp(now)].reset_index(drop=True)
        return df

    # ---------- orders ----------
    def enter(self, sig: Signal, qty: int, now: datetime) -> None:
        try:
            fill = self.broker.order(sig.symbol, "BUY", qty, sig.product, sig.entry)
        except Exception as e:
            self.store.log(f"ENTRY FAILED {sig.symbol}: {e}", "ERROR")
            return
        if sig.target and sig.product == "MIS":       # target = R-multiple of the ACTUAL fill, as in backtest
            p = self.cfg["strategies"]["intraday_orb"]
            sig.target = fill + p["target_r"] * (fill - sig.stop)
        self.store.upsert_position({
            "symbol": sig.symbol, "product": sig.product, "qty": qty, "entry": fill,
            "stop": sig.stop, "target": sig.target or 0.0, "strategy": sig.strategy,
            "opened_at": now.isoformat(timespec="seconds"), "peak": fill})
        self.store.add_trade({"ts": now.isoformat(timespec="seconds"), "symbol": sig.symbol,
                              "side": "BUY", "qty": qty, "price": fill, "product": sig.product,
                              "strategy": sig.strategy, "pnl": -self.fee(fill, qty),
                              "reason": sig.reason, "mode": self.broker.name})
        self.store.log(f"BUY {sig.symbol} x{qty} @ {fill:.2f} stop {sig.stop:.2f} "
                       f"tgt {sig.target or 0:.2f} [{sig.strategy}] {sig.reason}")

    def exit(self, pos: dict, price: float, reason: str, now: datetime) -> None:
        try:
            fill = self.broker.order(pos["symbol"], "SELL", pos["qty"], pos["product"], price)
        except Exception as e:
            self.store.log(f"EXIT FAILED {pos['symbol']}: {e}", "ERROR")
            return
        pnl = (fill - pos["entry"]) * pos["qty"] - self.fee(fill, pos["qty"])
        self.store.delete_position(pos["symbol"])
        self.store.add_trade({"ts": now.isoformat(timespec="seconds"), "symbol": pos["symbol"],
                              "side": "SELL", "qty": pos["qty"], "price": fill,
                              "product": pos["product"], "strategy": pos["strategy"],
                              "pnl": pnl, "reason": reason, "mode": self.broker.name})
        self.store.log(f"SELL {pos['symbol']} x{pos['qty']} @ {fill:.2f} pnl {pnl:+.0f} ({reason})")

    # ---------- tick ----------
    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        t = now.time()
        if now.weekday() >= 5 or not (_t(self.m["open"]) <= t <= _t(self.m["close"])):
            return
        positions = self.store.positions()
        syms = sorted(set(self.universe) | set(positions))
        prices = self.broker.ltp(syms)
        day_pnl, week_pnl = self.pnl(now)
        # size and limit on current equity (capital + realized P&L), exactly as the backtests do
        self.risk.capital = self.cfg.capital + self.store.realized_pnl("0000")

        self.manage_exits(positions, prices, now, flatten=self.risk.halted(day_pnl, week_pnl))
        positions = self.store.positions()
        if self.paused() or self.risk.halted(day_pnl, week_pnl):
            return
        self.scan_entries(positions, prices, now, day_pnl, week_pnl)

    def manage_exits(self, positions: dict, prices: dict, now: datetime, flatten: bool) -> None:
        sq = now.time() >= _t(self.m["intraday_squareoff"])
        for sym, pos in positions.items():
            px = prices.get(sym)
            if px is None:
                continue
            if pos["product"] == "MIS":
                if px <= pos["stop"]:
                    self.exit(pos, px, "stop", now)
                elif pos["target"] and px >= pos["target"]:
                    self.exit(pos, px, "target", now)
                elif sq or flatten:
                    self.exit(pos, px, "squareoff" if sq else "loss-limit halt", now)
            else:  # CNC swing: stop live; trail/EMA/max-hold decided on yesterday's completed bar
                if px <= pos["stop"]:
                    self.exit(pos, px, "stop", now)
                    continue
                today = now.strftime("%Y-%m-%d")
                if now.time() < _t("09:20") or self.store.get(f"swing_eval:{sym}") == today:
                    continue
                if pos["opened_at"][:10] == today:
                    continue
                self.store.set(f"swing_eval:{sym}", today)
                df = self.daily(sym, now)
                if len(df) > 60:
                    last_close = float(df["close"].iloc[-1])
                    out, trail, why = swing_exit(pos, df, last_close, self.cfg["strategies"]["swing_trend"],
                                                 pd.Timestamp(df["date"].iloc[-1]))
                    if out:
                        self.exit(pos, px, why, now)          # next-open execution, as in backtest
                    else:
                        pos.update(stop=max(trail, pos["stop"]), peak=max(last_close, pos.get("peak") or 0))
                        self.store.upsert_position(pos)

    def scan_entries(self, positions: dict, prices: dict, now: datetime,
                     day_pnl: float, week_pnl: float) -> None:
        st = self.cfg["strategies"]
        trades_today = len([x for x in self.store.trades(now.strftime("%Y-%m-%d")) if x["side"] == "BUY"])
        cash = self.cash(positions)
        signals: list[Signal] = []

        if st["intraday_orb"]["enabled"] and _t(self.m["intraday_entry_start"]) <= now.time() <= _t(self.m["intraday_entry_end"]):
            for sym in self.universe:
                if sym in positions:
                    continue
                if self.store.get(f"orb_done:{sym}") == now.strftime("%Y-%m-%d"):
                    continue                                   # one ORB attempt per stock per day
                try:
                    bars = self.bars5(sym, now)
                    s = intraday_orb(sym, bars, st["intraday_orb"], pd.Timestamp(now))
                except Exception as e:
                    self.store.log(f"scan {sym}: {e}", "WARN")
                    continue
                if s:
                    signals.append(s)

        today = now.strftime("%Y-%m-%d")
        if (st["swing_trend"]["enabled"] and now.time() >= _t("09:20")
                and self.store.get("swing_scan_day") != today):
            self.store.set("swing_scan_day", today)
            for sym in self.universe:
                if sym in positions:
                    continue
                try:
                    df = self.daily(sym, now)              # completed bars: signal from yesterday's close
                    s = swing_trend(sym, df, st["swing_trend"])
                    if s and prices.get(sym):
                        s.entry = float(prices[sym])      # execute at market ~ today's open
                except Exception as e:
                    self.store.log(f"swing scan {sym}: {e}", "WARN")
                    continue
                if s:
                    signals.append(s)

        for s in signals:
            positions = self.store.positions()
            d = self.risk.approve(s, positions, cash, day_pnl, week_pnl, trades_today)
            if not d.ok:
                self.store.log(f"skip {s.symbol} [{s.strategy}]: {d.why}")
                continue
            if self.cfg["research"]["llm_veto"]:
                ok, note = research.veto(s, self.cfg["research"]["model"])
                if not ok:
                    self.store.log(f"VETO {s.symbol}: {note}")
                    continue
            if s.entry <= s.stop:
                self.store.log(f"skip {s.symbol}: gapped below stop")
                continue
            if s.product == "MIS":
                self.store.set(f"orb_done:{s.symbol}", now.strftime("%Y-%m-%d"))
            self.enter(s, d.qty, now)
            trades_today += 1
            cash = self.cash(self.store.positions())

    def run_forever(self) -> None:
        self.store.log(f"engine start mode={self.broker.name} capital={self.cfg.capital}")
        while True:
            try:
                self.tick()
            except Exception as e:
                self.store.log(f"tick error: {e}", "ERROR")
            time.sleep(self.m["tick_seconds"])
