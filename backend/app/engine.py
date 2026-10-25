"""The trading cycle and model training. Every stage writes events, which the dashboard streams live.

Cycle: session -> universe -> market -> signals -> debate -> risk -> execute -> reconcile
Modes: test (simulated fills, test book) | automatic (real Kite orders, live book; needs two-step check)
"""
from __future__ import annotations

import json
import logging
import math
import secrets
import threading
from datetime import datetime, timedelta

import pandas as pd

from deskml.agents import Candidate, Decision, DecisionDesk, DeskConfig, Prices, Spend
from deskml.features import build_features
from deskml.model import Registry, blend, train_and_evaluate
from deskml.signals import live_snapshot, stance_of

from .broker import Ledger, LiveBroker, PaperBroker
from .config import settings
from .market import NIFTY50_KEY, NIFTY50_TOKEN, SessionMissing, make_gateway, make_order_gateway
from .notify import notify
from .security import TwoStep
from .store import Store, now_ist, ts
from .universe import Stock, build_universe

log = logging.getLogger(__name__)

STAGES = ["session", "universe", "market", "signals", "debate", "risk", "execute", "reconcile"]
STAGE_LABELS = {
    "session": "Kite session", "universe": "Universe", "market": "Market data", "signals": "Signals + model",
    "debate": "Agent debate", "risk": "Risk check", "execute": "Orders", "reconcile": "Portfolio",
}
MODES = ("test", "automatic")


class Engine:
    def __init__(self, store: Store):
        self.store = store
        self.gw = make_gateway(store)
        self.orders_gw = make_order_gateway(store, self.gw)
        self.ledgers = {"test": Ledger(store, "test", settings.paper_capital),
                        "live": Ledger(store, "live", settings.live_capital or 0.0)}
        self.paper = PaperBroker(self.ledgers["test"])
        self.live = LiveBroker(self.ledgers["live"], self.orders_gw, demo=self.gw.demo)
        prices = Prices(settings.price_quick_in, settings.price_quick_out, settings.price_deep_in,
                        settings.price_deep_out, settings.price_web_search_1k)
        self.spend = Spend(settings.claude_daily_budget_usd,
                           lambda: store.get(f"claude_spend:{now_ist().date().isoformat()}", 0.0),
                           lambda v: store.set(f"claude_spend:{now_ist().date().isoformat()}", v))
        self.desk = DecisionDesk(DeskConfig(settings.anthropic_api_key, settings.deep_model, settings.quick_model,
                                            settings.use_web_news, settings.allow_intraday_shorts, prices),
                                 self.spend)
        self.registry = Registry(settings.model_dir)
        self.twostep = TwoStep(store)
        self._lock = threading.Lock()
        self._train_lock = threading.Lock()
        self._universe: list[Stock] = []
        self._universe_day: str | None = None
        self._bench: pd.Series | None = None
        if store.get("trading_mode") not in MODES:
            store.set("trading_mode", "test")
        if store.get("halted") is None:
            store.set("halted", False)

    # ------------------------------------------------------------------ modes
    @property
    def mode(self) -> str:
        m = self.store.get("trading_mode", "test")
        if m == "automatic":
            until = self.store.get("armed_until")
            if until and now_ist().isoformat() >= until:
                self._to_test("Automatic mode expired for the day - verify again to resume real orders")
                return "test"
        return m

    @property
    def book(self) -> str:
        return "live" if self.mode == "automatic" else "test"

    @property
    def ledger(self) -> Ledger:
        return self.ledgers[self.book]

    @property
    def broker(self):
        return self.live if self.book == "live" else self.paper

    def _to_test(self, why: str) -> None:
        self.store.set("trading_mode", "test")
        self.store.set("armed_until", None)
        self.store.event(None, "security", "done", why)
        notify("Back in Test mode", why)

    def set_test_mode(self) -> None:
        if self.store.get("trading_mode") != "test":
            self._to_test("Switched to Test mode - orders are simulated again")

    def arm_automatic(self, password: str, code: str) -> dict:
        ok, why = self.live.preflight()
        if not ok:
            raise PermissionError(f"Automatic mode isn't available: {why}")
        if not self.gw.is_ready():
            raise PermissionError("Log in to Kite first")
        if not self.orders_gw.is_ready():
            raise PermissionError("Log in to Kite for orders first (Controls, Kite login)")
        self.twostep.check(password, code)
        led = self.ledgers["live"]
        if led.start_capital <= 0:
            led.reset(settings.live_capital or 0.0)
        self.store.set("trading_mode", "automatic")
        self.store.set("armed_at", ts())
        until = None
        if settings.auto_reverify_daily:
            until = (now_ist() + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0).isoformat()
        self.store.set("armed_until", until)
        self.store.event(None, "security", "done", "Automatic mode ON - real orders will be placed (two-step verified)")
        notify("Automatic mode ON", f"Real orders enabled. Budget ₹{led.cash:,.0f}.", priority="high")
        return self.status()

    # ------------------------------------------------------------------ status
    @property
    def halted(self) -> bool:
        return bool(self.store.get("halted", False))

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def status(self) -> dict:
        sess = self.gw.session()
        ok, why = self.live.preflight()
        model = self.registry.active()
        return {
            "demo": self.gw.demo, "mode": self.mode, "book": self.book, "halted": self.halted,
            "running": self.running, "training": self._train_lock.locked(),
            "automatic": {"available": ok, "blocked_reason": why, "armed_at": self.store.get("armed_at"),
                          "armed_until": self.store.get("armed_until"), "live_capital": settings.live_capital,
                          "reverify_daily": settings.auto_reverify_daily},
            "twostep": {"enabled": self.twostep.enabled, "locked_until": self.twostep.locked_until()},
            "engine": self.desk.engine,
            "model": ({"version": model.version, "auc": model.metrics.get("holdout", {}).get("auc"),
                       "trained_until": model.trained_until} if model else None),
            "ml_weight": settings.ml_weight,
            "session": {"ok": sess is not None, **(sess or {})},
            "data_source": settings.data_mode,
            "orders_session": ({"configured": True, "ok": self.orders_gw.is_ready(), **(self.orders_gw.session() or {})}
                               if self.orders_gw is not None and self.orders_gw is not self.gw
                               else {"configured": self.orders_gw is not None, "ok": sess is not None, "same": True}),
            "claude": {"spent_today_usd": round(self.spend.today, 3), "daily_cap_usd": settings.claude_daily_budget_usd,
                       "refresh_min": settings.debate_refresh_min},
            "universe_source": self.store.get("universe_source", ""),
            "universe_index": settings.universe_index,
            "risk_limits": {"max_position_pct": settings.max_position_pct,
                            "daily_loss_limit_pct": settings.daily_loss_limit_pct,
                            "stop_loss_pct": settings.stop_loss_pct, "max_positions": settings.max_positions},
            "cycle_minutes": settings.cycle_minutes,
            "last_run": self.store.one("SELECT * FROM runs ORDER BY id DESC LIMIT 1"),
        }

    # ------------------------------------------------------------------ login
    def new_login_link(self, kind: str = "data") -> str:
        """kind='data' logs in the market-data source; kind='orders' the Kite Connect order API."""
        gw = self.orders_gw if kind == "orders" else self.gw
        if gw is None:
            raise RuntimeError("No Kite Connect API key is set for orders")
        state = secrets.token_urlsafe(16)
        self.store.set("login_state", {"state": state, "ts": ts()})
        return gw.login_url(state)

    def send_login_to_phone(self, reason: str = "Kite login needed for today") -> list[str]:
        self.store.set("last_login_push", ts())
        sent: list[str] = []
        if not self.gw.is_ready():
            sent += notify("Kite login needed", f"{reason}. Tap to log in with Kite; the agent resumes by itself.",
                           self.new_login_link("data"), priority="high")
        needs_orders = (self.orders_gw is not None and self.orders_gw is not self.gw
                        and (self.mode == "automatic" or settings.live_trading_enabled)
                        and not self.orders_gw.is_ready())
        if needs_orders:
            sent += notify("Kite orders login", "Second login for placing real orders (Kite Connect).",
                           self.new_login_link("orders"), priority="high")
        return sorted(set(sent))

    # ------------------------------------------------------------------ cycle
    def run_cycle(self, trigger: str = "schedule") -> int | None:
        if not self._lock.acquire(blocking=False):
            return None
        try:
            return self._run(trigger)
        finally:
            self._lock.release()

    def _universe_now(self) -> list[Stock]:
        today = now_ist().date().isoformat()
        if self._universe_day != today or not self._universe:
            self._universe, src = build_universe(self.gw)
            self._universe_day = today
            self.store.set("universe_source", src)
        return self._universe

    def _run(self, trigger: str) -> int:
        book = self.book
        run_id = int(self.store.execute("INSERT INTO runs(started,status,trigger,book) VALUES(?,?,?,?)",
                                        (ts(), "running", trigger, book)).lastrowid)
        e = lambda st, s, m, d=None: self.store.event(run_id, st, s, m, d)  # noqa: E731
        summary: dict = {"book": book}
        led = self.ledgers[book]
        try:
            if self.halted:
                e("session", "blocked", "Agent is stopped - nothing runs until you resume")
                return self._finish(run_id, "halted", summary)
            e("session", "active", "Checking Kite session")
            if not self.gw.is_ready():
                last = self.store.get("last_login_push")
                if not last or now_ist() - datetime.fromisoformat(last) > timedelta(minutes=30):
                    sent = self.send_login_to_phone()
                    e("session", "blocked", "No Kite session today - login link sent to " +
                      (", ".join(sent) if sent else "the dashboard (no phone alerts set up)"))
                else:
                    e("session", "blocked", "Waiting for you to log in to Kite (link already sent)")
                return self._finish(run_id, "needs_login", summary)
            sess = self.gw.session() or {}
            e("session", "done", f"Logged in as {sess.get('user_id')}. "
              f"{'Automatic mode: real orders' if book == 'live' else 'Test mode: simulated orders'}")

            e("universe", "active", "Loading tradable stocks")
            uni = self._universe_now()
            sectors = {s.sector for s in uni}
            e("universe", "done", f"{len(uni)} stocks across {len(sectors)} sectors "
              f"({self.store.get('universe_source', '')})", {"count": len(uni)})

            e("market", "active", "Fetching live quotes and daily history")
            quotes = self.gw.quotes([s.key for s in uni] + [NIFTY50_KEY])
            bench_df = self.gw.daily_candles(NIFTY50_TOKEN)
            self._bench = bench_df["close"] if len(bench_df) else None
            nifty = quotes.get(NIFTY50_KEY, {})
            hist: dict[str, pd.DataFrame] = {}
            for i, s in enumerate(uni):
                try:
                    hist[s.key] = self.gw.daily_candles(s.token)
                except SessionMissing:
                    raise
                except Exception as ex:  # noqa: BLE001
                    log.warning("history failed for %s: %s", s.symbol, ex)
                if i and i % 50 == 0:
                    e("market", "active", f"History loaded for {i}/{len(uni)} stocks")
            nchg = None
            if nifty.get("last_price") and (nifty.get("ohlc") or {}).get("close"):
                nchg = (nifty["last_price"] / nifty["ohlc"]["close"] - 1) * 100
            summary["nifty_change"] = nchg
            e("market", "done", f"{len(quotes) - 1} quotes, {len(hist)} histories"
              + (f"; Nifty {nchg:+.2f}%" if nchg is not None else ""), {"nifty_change": nchg})

            snaps = self._signals(run_id, uni, hist, quotes, e)
            bull = sum(1 for x in snaps.values() if x["stance"] == "bullish")
            bear = sum(1 for x in snaps.values() if x["stance"] == "bearish")
            summary.update(scored=len(snaps), bullish=bull, bearish=bear)

            by_key = {s.key: s for s in uni}
            held = {f"{p['exchange']}:{p['symbol']}": p for p in led.positions()}
            exits = self._stop_target_exits(run_id, book, held, snaps)
            ranked = sorted((k for k in snaps if k not in held and snaps[k]["stance"] == "bullish"),
                            key=lambda k: -snaps[k]["score"])
            picks = ranked[: settings.candidates_per_cycle]
            if settings.allow_intraday_shorts:
                shorts = sorted((k for k in snaps if k not in held and snaps[k]["stance"] == "bearish"),
                                key=lambda k: snaps[k]["score"])
                picks += shorts[: max(1, settings.candidates_per_cycle // 2)]
            review = [k for k in held if k in snaps and k not in exits] + picks
            e("debate", "active", f"Desk reviewing {len(review)} stocks ({len(held)} held, {len(picks)} new) "
              f"with {'Claude' if self.desk.engine == 'claude' else 'the rule set'}",
              {"symbols": [k.split(':')[1] for k in review]})
            decisions = []
            for k in review:
                s, snap = by_key.get(k), snaps[k]
                if s is None:
                    p = held[k]
                    s = Stock(p["symbol"], p["exchange"], p["sector"], 0)
                pos = held.get(k)
                cand = Candidate(s.symbol, s.exchange, s.sector, snap["ltp"], snap["score"], snap["stance"],
                                 snap["reasons"], snap["features"], pos["qty"] if pos else 0,
                                 pos["avg_price"] if pos else 0.0, self._memory(book, s.symbol),
                                 ml_prob=snap.get("ml_prob"), rule_score=snap.get("rule_score"))
                d = self._cached_decision(book, cand)
                if d is None:
                    d = self.desk.decide(cand, lambda st, m, dd=None: e("debate", "active" if st == "info" else st, m, dd))
                    self._remember_decision(book, cand, d)
                else:
                    e("debate", "active", f"{s.symbol}: reusing the debate from {d.debate.get('_at', '')[11:16]} "
                      "(score has not moved much)")
                decisions.append((cand, d))
                e("debate", "active", f"{s.symbol}: {d.action} (conviction {d.conviction:.0%})",
                  {"symbol": s.symbol, "action": d.action})
            e("debate", "done", f"{sum(1 for _, d in decisions if d.action in ('BUY', 'SELL'))} trade ideas "
              f"from {len(decisions)} reviews")

            e("risk", "active", "Sizing and checking limits")
            ideas = list(exits.values()) + self._size_and_check(run_id, book, decisions, held, e)
            e("risk", "done", f"{len(ideas)} orders cleared", {"ideas": len(ideas)})

            if ideas:
                e("execute", "active", f"Sending {len(ideas)} {'REAL' if book == 'live' else 'test'} orders")
                for i in ideas:
                    self.execute_idea(i["id"], run_id)
                e("execute", "done", f"{len(ideas)} orders processed")
            else:
                e("execute", "done", "No orders this cycle")

            if book == "live":
                for note in self.live.reconcile():
                    e("reconcile", "warn", note)
                    notify("Check your Kite holdings", note)
            eq = self.snapshot_equity(book)
            summary.update(equity=eq["equity"], cash=eq["cash"])
            e("reconcile", "done", f"{'Live' if book == 'live' else 'Test'} book: equity ₹{eq['equity']:,.0f}, "
              f"cash ₹{eq['cash']:,.0f}, {len(led.positions())} positions", eq)
            return self._finish(run_id, "ok", summary)
        except SessionMissing:
            e("session", "blocked", "Kite session expired mid-run - login link sent")
            self.send_login_to_phone("Kite session expired")
            return self._finish(run_id, "needs_login", summary)
        except Exception as ex:  # noqa: BLE001
            log.exception("cycle failed")
            e("reconcile", "error", f"Cycle failed: {ex}")
            notify("Agent error", f"Cycle {run_id} failed: {ex}")
            return self._finish(run_id, "error", summary)

    def _signals(self, run_id, uni, hist, quotes, e) -> dict[str, dict]:
        model = self.registry.active()
        e("signals", "active", "Scoring every stock" + (f" with model {model.version}" if model else ""))
        snaps: dict[str, dict] = {}
        min_turn = 10.0 if settings.universe_index.lower() == "all" else 0.0
        feats = []
        for s in uni:
            snap = live_snapshot(hist.get(s.key), quotes.get(s.key), self._bench)
            if not snap or (snap["features"].get("turnover_cr") or 0) < min_turn:
                continue
            snaps[s.key] = snap
            if model:
                x = build_features(snap["frame"].iloc[-300:], self._bench).iloc[[-1]].copy()
                x.index = [s.key]
                feats.append(x)
        probs: dict[str, float] = {}
        if model and feats:
            X = pd.concat(feats)
            probs = dict(zip(X.index, model.predict(X)))
        for s in uni:
            snap = snaps.get(s.key)
            if not snap:
                continue
            snap.pop("frame", None)
            rule = snap["score"]
            prob = probs.get(s.key)
            snap["rule_score"] = rule
            snap["ml_prob"] = round(float(prob), 4) if prob is not None else None
            snap["score"] = blend(rule, prob, settings.ml_weight)
            snap["stance"] = stance_of(snap["score"])
            if prob is not None:
                snap["reasons"] = snap["reasons"] + [f"model {prob:.0%} chance of beating costs in 5 days"]
            self.store.execute(
                "INSERT OR REPLACE INTO signals(symbol,exchange,sector,ltp,change_pct,score,rule_score,ml_prob,stance,"
                "features,run_id,ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (s.symbol, s.exchange, s.sector, snap["ltp"], snap["change_pct"], snap["score"], rule,
                 snap["ml_prob"], snap["stance"],
                 json.dumps({**snap["features"], "reasons": snap["reasons"], "spark": snap["spark"]}), run_id, ts()))
        bull = sum(1 for x in snaps.values() if x["stance"] == "bullish")
        bear = sum(1 for x in snaps.values() if x["stance"] == "bearish")
        e("signals", "done", f"{bull} bullish, {bear} bearish, {len(snaps) - bull - bear} neutral"
          + (f" (rules {1 - settings.ml_weight:.0%} + model {settings.ml_weight:.0%})" if model else " (rules only)"),
          {"bullish": bull, "bearish": bear, "neutral": len(snaps) - bull - bear})
        return snaps

    def _cached_decision(self, book: str, c: Candidate) -> Decision | None:
        """Reuse a recent Claude debate when nothing material changed, to keep API spend down."""
        if self.desk.engine != "claude":
            return None
        rec = self.store.get(f"last_debate:{book}:{c.symbol}")
        if not rec:
            return None
        age_min = (now_ist() - datetime.fromisoformat(rec["at"])).total_seconds() / 60
        if age_min > settings.debate_refresh_min or abs(c.score - rec["score"]) >= settings.debate_score_delta \
                or rec["held_qty"] != c.held_qty:
            return None
        d = Decision(**rec["decision"])
        d.engine = "claude (reused)"
        d.debate = {**d.debate, "_at": rec["at"]}
        return d

    def _remember_decision(self, book: str, c: Candidate, d: Decision) -> None:
        if d.engine == "claude":
            self.store.set(f"last_debate:{book}:{c.symbol}",
                           {"at": now_ist().isoformat(timespec="seconds"), "score": c.score, "held_qty": c.held_qty,
                            "decision": d.__dict__})

    def _finish(self, run_id: int, status: str, summary: dict) -> int:
        self.store.execute("UPDATE runs SET finished=?, status=?, summary=? WHERE id=?",
                           (ts(), status, json.dumps(summary), run_id))
        self.store.event(run_id, "cycle", status, f"Cycle {run_id} finished: {status}", summary)
        return run_id

    def _memory(self, book: str, symbol: str) -> list[str]:
        rows = self.store.query(
            "SELECT o.ts, o.side, o.fill_price, o.pnl, i.rationale FROM orders o LEFT JOIN ideas i ON i.id=o.idea_id "
            "WHERE o.book=? AND o.symbol=? AND o.status='COMPLETE' ORDER BY o.id DESC LIMIT 6", (book, symbol))
        out = []
        for r in rows:
            pnl = f", realised ₹{r['pnl']:+,.0f}" if r["pnl"] else ""
            out.append(f"{r['ts'][:10]} {r['side']} @ ₹{r['fill_price']:.2f}{pnl}: {(r['rationale'] or '')[:140]}")
        return out

    def _stop_target_exits(self, run_id: int, book: str, held: dict, snaps: dict) -> dict[str, dict]:
        exits = {}
        for k, p in held.items():
            snap = snaps.get(k)
            if not snap or p["qty"] <= 0:
                continue
            ltp = snap["ltp"]
            stop = p["stop"] or (p["avg_price"] * (1 - settings.stop_loss_pct / 100) if settings.stop_loss_pct else None)
            why = None
            if stop and ltp <= stop:
                why = f"Stop hit at ₹{ltp:.2f} (stop ₹{stop:.2f})"
            elif p["target"] and ltp >= p["target"]:
                why = f"Target reached at ₹{ltp:.2f} (target ₹{p['target']:.2f})"
            if why:
                exits[k] = self._new_idea(run_id, book, p["symbol"], p["exchange"], p["sector"], "SELL",
                                          abs(p["qty"]), ltp, 1.0, 0, why, {}, p.get("product") or settings.product,
                                          engine="rule")
        return exits

    def _new_idea(self, run_id, book, symbol, exchange, sector, action, qty, price, conviction, size_pct, rationale,
                  debate, product, stop=None, target=None, engine=None, status="proposed", reason="") -> dict:
        cur = self.store.execute(
            "INSERT INTO ideas(run_id,ts,book,symbol,exchange,sector,action,qty,price,conviction,size_pct,rationale,"
            "debate,engine,status,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, ts(), book, symbol, exchange, sector, action, qty, price, conviction, size_pct, rationale,
             json.dumps({**debate, "product": product, "stop": stop, "target": target}),
             engine or self.desk.engine, status, reason))
        return {"id": int(cur.lastrowid), "symbol": symbol, "action": action, "qty": qty}

    def equity_now(self, book: str) -> float:
        led = self.ledgers[book]
        val = led.cash
        for p in led.positions():
            sig = self.store.one("SELECT ltp FROM signals WHERE symbol=? AND exchange=?", (p["symbol"], p["exchange"]))
            val += p["qty"] * (sig["ltp"] if sig else p["avg_price"])
        return val

    def _size_and_check(self, run_id, book, decisions, held, e) -> list[dict]:
        led = self.ledgers[book]
        equity = self.equity_now(book)
        day_key = f"day_start_equity:{book}:{now_ist().date().isoformat()}"
        start = float(self.store.get(day_key, equity))
        self.store.set(day_key, start)
        lim = settings.daily_loss_limit_pct
        allow_new = not (lim and equity < start * (1 - lim / 100))
        if not allow_new:
            e("risk", "blocked", f"Daily loss limit {lim}% hit - no new buys today")
        open_count, cash, out = len(held), led.cash, []
        for c, d in decisions:
            key = f"{c.exchange}:{c.symbol}"
            product = "MIS" if d.horizon == "intraday" and settings.allow_intraday_shorts else settings.product
            if d.action == "SELL" and c.held_qty > 0:
                out.append(self._new_idea(run_id, book, c.symbol, c.exchange, c.sector, "SELL", c.held_qty, c.ltp,
                                          d.conviction, 0, d.rationale, d.debate,
                                          held[key].get("product") or product, engine=d.engine))
                continue
            if d.action == "BUY" and c.held_qty < 0:
                out.append(self._new_idea(run_id, book, c.symbol, c.exchange, c.sector, "BUY", -c.held_qty, c.ltp,
                                          d.conviction, 0, d.rationale, d.debate, "MIS", engine=d.engine))
                continue
            opening = (d.action == "BUY" and c.held_qty == 0) or (
                d.action == "SELL" and c.held_qty == 0 and settings.allow_intraday_shorts)
            if not opening:
                self._new_idea(run_id, book, c.symbol, c.exchange, c.sector, d.action, 0, c.ltp, d.conviction, 0,
                               d.rationale, d.debate, product, engine=d.engine, status="no_action")
                continue
            pct = min(d.size_pct, settings.max_position_pct) if settings.max_position_pct else d.size_pct
            alloc = min(equity * pct / 100, cash * 0.98)
            qty = int(math.floor(alloc / c.ltp)) if c.ltp > 0 else 0
            why = None
            if not allow_new:
                why = "daily loss limit"
            elif open_count >= settings.max_positions:
                why = f"already {open_count} positions (max {settings.max_positions})"
            elif qty < 1:
                why = f"size {pct:.1f}% buys less than one share"
            stop = round(c.ltp * (1 - d.stop_pct / 100), 2) if d.stop_pct else None
            target = round(c.ltp * (1 + d.target_pct / 100), 2) if d.target_pct else None
            if d.action == "SELL":
                stop, target, product = (round(c.ltp * (1 + d.stop_pct / 100), 2) if d.stop_pct else None), None, "MIS"
            idea = self._new_idea(run_id, book, c.symbol, c.exchange, c.sector, d.action, max(qty, 0), c.ltp,
                                  d.conviction, pct, d.rationale, d.debate, product, stop, target, engine=d.engine,
                                  status="skipped" if why else "proposed", reason=why or "")
            if why:
                e("risk", "warn", f"{c.symbol} skipped: {why}")
                continue
            cash -= qty * c.ltp
            open_count += 1
            out.append(idea)
        return out

    def execute_idea(self, idea_id: int, run_id: int | None = None) -> dict:
        idea = self.store.one("SELECT * FROM ideas WHERE id=?", (idea_id,))
        if not idea:
            return {"ok": False, "note": "idea not found"}
        book = idea["book"]
        broker = self.live if book == "live" else self.paper
        led = self.ledgers[book]
        meta = json.loads(idea["debate"] or "{}")
        product = meta.get("product") or settings.product
        ref = idea["price"]
        try:
            q = self.gw.quotes([f"{idea['exchange']}:{idea['symbol']}"])
            ref = float(next(iter(q.values()))["last_price"]) if q else ref
        except Exception:  # noqa: BLE001
            pass
        fill = broker.execute(idea["symbol"], idea["exchange"], idea["action"], int(idea["qty"]), ref, product)
        pnl = None
        if fill.ok:
            pnl = led.apply(idea["symbol"], idea["exchange"], idea["sector"], idea["action"], fill.qty, fill.price,
                            fill.charges, product, meta.get("stop"), meta.get("target"))
        self.store.execute(
            "INSERT INTO orders(ts,book,idea_id,symbol,exchange,side,qty,product,ref_price,fill_price,charges,status,"
            "kite_order_id,note,pnl) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts(), book, idea_id, idea["symbol"], idea["exchange"], idea["action"], fill.qty or idea["qty"], product,
             ref, fill.price or None, fill.charges, fill.status, fill.order_id, fill.note, pnl))
        self.store.execute("UPDATE ideas SET status=?, reason=? WHERE id=?",
                           ("executed" if fill.ok else "failed", fill.note, idea_id))
        label = "REAL" if book == "live" else "test"
        msg = (f"{idea['action']} {fill.qty} {idea['symbol']} @ ₹{fill.price:,.2f} ({label})" if fill.ok
               else f"{idea['action']} {idea['symbol']} not filled: {fill.note}")
        self.store.event(run_id or idea["run_id"], "execute", "done" if fill.ok else "error", msg,
                         {"symbol": idea["symbol"], "side": idea["action"], "book": book})
        if book == "live" or not fill.ok:
            notify("Order " + ("filled" if fill.ok else "problem"), msg)
        return {"ok": fill.ok, "note": msg}

    def _exit_all(self, trigger: str, rationale: str, only_mis: bool = False) -> int:
        book = self.book
        led = self.ledgers[book]
        poss = [p for p in led.positions() if not only_mis or p.get("product") == "MIS"]
        if not poss:
            return 0
        run_id = int(self.store.execute("INSERT INTO runs(started,status,trigger,book) VALUES(?,?,?,?)",
                                        (ts(), "running", trigger, book)).lastrowid)
        for p in poss:
            side = "SELL" if p["qty"] > 0 else "BUY"
            sig = self.store.one("SELECT ltp FROM signals WHERE symbol=? AND exchange=?", (p["symbol"], p["exchange"]))
            idea = self._new_idea(run_id, book, p["symbol"], p["exchange"], p["sector"], side, abs(p["qty"]),
                                  sig["ltp"] if sig else p["avg_price"], 1.0, 0, rationale, {},
                                  p.get("product") or settings.product, engine="rule")
            self.execute_idea(idea["id"], run_id)
        self._finish(run_id, "ok", {trigger: len(poss)})
        return len(poss)

    def square_off_intraday(self) -> int:
        return self._exit_all("square-off", "Intraday square-off before close", only_mis=True)

    def flatten_all(self) -> int:
        return self._exit_all("flatten", "Closed by the stop switch")

    def fix_live_position(self, symbol: str, exchange: str, qty: int) -> None:
        self.ledgers["live"].set_qty(symbol, exchange, qty)
        self.store.event(None, "reconcile", "done", f"Live book: {symbol} set to {qty} shares by you")

    def snapshot_equity(self, book: str) -> dict:
        led = self.ledgers[book]
        invested = 0.0
        for p in led.positions():
            sig = self.store.one("SELECT ltp FROM signals WHERE symbol=? AND exchange=?", (p["symbol"], p["exchange"]))
            invested += p["qty"] * (sig["ltp"] if sig else p["avg_price"])
        snap = {"ts": ts(), "cash": round(led.cash, 2), "invested": round(invested, 2),
                "equity": round(led.cash + invested, 2), "realized": led.realized}
        self.store.execute("INSERT OR REPLACE INTO equity(book,ts,cash,invested,equity,realized) VALUES(?,?,?,?,?,?)",
                           (book, snap["ts"], snap["cash"], snap["invested"], snap["equity"], snap["realized"]))
        return snap

    # ------------------------------------------------------------------ model
    def history_for(self, stocks: list[Stock], days: int) -> tuple[dict[str, pd.DataFrame], dict[str, str],
                                                                   pd.DataFrame]:
        data, exch = {}, {}
        for s in stocks:
            try:
                data[s.symbol] = self.gw.daily_candles(s.token, days)
                exch[s.symbol] = s.exchange
            except SessionMissing:
                raise
            except Exception as ex:  # noqa: BLE001
                log.warning("history failed for %s: %s", s.symbol, ex)
        return data, exch, self.gw.daily_candles(NIFTY50_TOKEN, days)

    def train_model(self, trigger: str = "manual") -> dict | None:
        if not self._train_lock.acquire(blocking=False):
            return None
        try:
            e = lambda s, m, d=None: self.store.event(None, "model", s, m, d)  # noqa: E731
            if not self.gw.is_ready():
                e("blocked", "Model training needs a Kite session for history")
                return None
            uni = self._universe_now()
            e("active", f"Training: loading {settings.ml_history_days} days of history for {len(uni)} stocks")
            data, _, bench = self.history_for(uni, settings.ml_history_days)
            e("active", "Training gradient-boosted model with a time-ordered holdout")
            b = train_and_evaluate(data, bench["close"] if len(bench) else None)
            auc = b.metrics["holdout"].get("auc") or 0
            cur = self.registry.active()
            cur_auc = (cur.metrics.get("holdout", {}).get("auc") or 0) if cur else 0
            activate = auc >= settings.ml_min_auc and (cur is None or auc >= cur_auc - 0.005)
            self.registry.save(b, activate=activate)
            hold = b.metrics["holdout"]
            msg = (f"Model {b.version}: holdout AUC {auc:.3f}, top-decile hit rate {hold['top_decile_hit_rate']:.0%} "
                   f"vs base {hold['base_rate']:.0%}. " +
                   ("Now active." if activate else f"Not activated (needs AUC ≥ {settings.ml_min_auc}"
                    + (f" and ≥ current {cur_auc:.3f}" if cur else "") + ")."))
            e("done", msg, {"version": b.version, "activated": activate})
            if trigger != "manual":
                notify("Model retrained", msg)
            return {**b.meta(), "activated": activate}
        except Exception as ex:  # noqa: BLE001
            log.exception("training failed")
            self.store.event(None, "model", "error", f"Training failed: {ex}")
            return None
        finally:
            self._train_lock.release()
