from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from agent.broker import DataSource, KiteBroker, PaperBroker
from agent.config import Config
from agent.engine import Engine
from agent.risk import RiskManager
from agent.store import Store
from agent.strategies import Signal, swing_trend


def cfg():
    return Config.load("config.yaml")


def sig(entry=100.0, stop=98.0, sym="AAA", product="CNC"):
    return Signal(sym, "t", product, entry, stop, None, "x")


def test_sizing_respects_risk_and_position_cap():
    r = RiskManager(cfg()["risk"], 100000)
    # risk 0.75% = 750 / 2 per share = 375, but cap 20% = 20000/100 = 200
    assert r.size(sig()) == 200
    assert r.size(sig(entry=1000, stop=980)) == 20  # cap 20 shares; risk gives 37


def test_loss_limits_halt_entries():
    r = RiskManager(cfg()["risk"], 100000)
    assert not r.approve(sig(), {}, 100000, -2500, 0, 0).ok
    assert not r.approve(sig(), {}, 100000, 0, -6000, 0).ok
    assert r.approve(sig(), {}, 100000, 0, 0, 0).ok


def test_exposure_and_cash_limits():
    r = RiskManager(cfg()["risk"], 100000)
    held = {"Z": {"product": "MIS", "qty": 350, "entry": 100.0}}  # 35k of 40k MIS cap
    d = r.approve(sig(product="MIS"), held, 100000, 0, 0, 0)
    assert d.ok and d.qty == 50
    assert not r.approve(sig(), {}, 50, 0, 0, 0).ok  # not enough cash


def test_live_broker_locked_without_unlock(monkeypatch):
    monkeypatch.delenv("LIVE_UNLOCK", raising=False)
    c = cfg()
    c.raw["mode"] = "live"
    with pytest.raises(RuntimeError):
        KiteBroker(object(), {}, c)
    monkeypatch.setenv("LIVE_UNLOCK", "I_ACCEPT_REAL_MONEY_RISK")
    KiteBroker(object(), {}, c)  # now allowed


def trend_df(n=300, breakout=True):
    rng = np.random.default_rng(1)
    close = 100 * np.cumprod(1 + 0.001 + rng.normal(0, 0.004, n))
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=n, freq="B"),
                       "open": close, "high": close * 1.004, "low": close * 0.996,
                       "close": close, "volume": 1_000_000.0})
    if breakout:
        df.loc[n - 1, ["close", "high"]] = df["high"].iloc[-21:-1].max() * 1.01
        df.loc[n - 1, "volume"] = 2_000_000.0
    return df


def test_swing_signal_fires_on_breakout_only():
    p = cfg()["strategies"]["swing_trend"]
    s = swing_trend("AAA", trend_df(), p)
    assert s and s.stop < s.entry and s.product == "CNC"
    assert swing_trend("AAA", trend_df(breakout=False), p) is None


class FakeData(DataSource):
    def __init__(self, px):
        self.px = px

    def ltp(self, symbols):
        return {s: self.px[s] for s in symbols if s in self.px}

    def history(self, symbol, interval, days):
        return pd.DataFrame()


def test_engine_stop_and_squareoff(tmp_path):
    c = cfg()
    store = Store(str(tmp_path / "t.db"))
    data = FakeData({"AAA": 100.0, "BBB": 100.0})
    eng = Engine(c, PaperBroker(data, c["costs"]), store)
    now = datetime(2026, 11, 2, 11, 0)  # Monday
    eng.enter(sig(100, 98, "AAA", "MIS"), 10, now)
    eng.enter(sig(100, 98, "BBB", "MIS"), 10, now)
    data.px["AAA"] = 97.0  # stop hit
    eng.manage_exits(store.positions(), data.ltp(["AAA", "BBB"]), now, flatten=False)
    assert set(store.positions()) == {"BBB"}
    eng.manage_exits(store.positions(), data.ltp(["BBB"]), datetime(2026, 11, 2, 15, 13), flatten=False)
    assert store.positions() == {}
    assert store.realized_pnl("0000") < 0


def test_pause_blocks_entries(tmp_path):
    c = cfg()
    store = Store(str(tmp_path / "p.db"))
    store.set("paused", True)
    eng = Engine(c, PaperBroker(FakeData({"RELIANCE": 100.0}), c["costs"]), store)
    eng.tick(datetime(2026, 11, 2, 11, 0))
    assert store.positions() == {}


def test_engine_disables_unvalidated_strategies(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("AGENT_VALIDATION", str(tmp_path / "none.json"))
    c = cfg()
    eng = Engine(c, PaperBroker(FakeData({}), c["costs"]), Store(str(tmp_path / "a.db")))
    assert not any(s["enabled"] for s in eng.cfg["strategies"].values())
    vf = tmp_path / "v.json"
    vf.write_text(json.dumps({"strategies": {"swing_trend": {"passed": True, "best_params": {"breakout_lookback": 55}},
                                             "intraday_orb": {"passed": False, "best_params": {}}}}))
    monkeypatch.setenv("AGENT_VALIDATION", str(vf))
    c = cfg()
    eng = Engine(c, PaperBroker(FakeData({}), c["costs"]), Store(str(tmp_path / "b.db")))
    assert eng.cfg["strategies"]["swing_trend"]["enabled"] and eng.cfg["strategies"]["swing_trend"]["breakout_lookback"] == 55
    assert not eng.cfg["strategies"]["intraday_orb"]["enabled"]
