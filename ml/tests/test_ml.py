from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from deskml.agents import Candidate, DecisionDesk, DeskConfig
from deskml.agents.desk import prompt
from deskml.backtest import BTParams, run_backtest
from deskml.costs import charges
from deskml.features import FEATURES, HORIZON, build_features, forward_return, label
from deskml.model import Registry, blend, train_and_evaluate, walk_forward


def make(seed: int, n: int = 700, edge: float = 0.0) -> pd.DataFrame:
    r = np.random.default_rng(seed)
    e = r.normal(0.0002, 0.015, n)
    ret = np.zeros(n)
    for i in range(n):
        ret[i] = e[i] + edge * ret[max(0, i - 5):i].sum() / 5
    c = 100 * np.exp(np.cumsum(ret))
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame({"open": c * (1 + r.normal(0, .002, n)), "high": c * 1.01, "low": c * .99, "close": c,
                         "volume": r.integers(100_000, 1_000_000, n)}, index=idx)


def test_features_do_not_look_ahead():
    df = make(1)
    full = build_features(df)
    cut = build_features(df.iloc[:400])
    pd.testing.assert_frame_equal(full.iloc[:400], cut, check_freq=False)
    assert list(full.columns) == FEATURES


def test_label_is_forward_and_net_of_costs():
    df = make(2)
    fr = forward_return(df)
    t = df.index[100]
    assert fr[t] == pytest.approx(df["close"].iloc[100 + HORIZON] / df["open"].iloc[101] - 1)
    y = label(df)
    assert y.iloc[-HORIZON:].isna().all()
    assert set(y.dropna().unique()) <= {0.0, 1.0}


def test_learns_planted_signal_but_not_noise():
    patterned = {f"S{i}": make(i, edge=0.6) for i in range(20)}
    noise = {f"S{i}": make(100 + i) for i in range(20)}
    good = train_and_evaluate(patterned, None)
    bad = train_and_evaluate(noise, None)
    assert good.metrics["holdout"]["auc"] > 0.6
    assert abs(bad.metrics["holdout"]["auc"] - 0.5) < 0.06


def test_walk_forward_never_trains_on_the_future(monkeypatch):
    import deskml.model as m
    seen = []
    real_fit = m.make_estimator

    class Spy:
        def __init__(self):
            self.est = real_fit()

        def fit(self, X, y):
            seen.append(len(X))
            self.est.fit(X, y)
            return self

        def predict_proba(self, X):
            return self.est.predict_proba(X)

    monkeypatch.setattr(m, "make_estimator", lambda seed=7: Spy())
    data = {f"S{i}": make(i, n=600) for i in range(6)}
    preds = walk_forward(data, None, retrain_every=100, min_train_days=250)
    assert len(seen) >= 2 and seen == sorted(seen)          # training sets only grow forward
    dates = sorted(preds["date"].unique())
    assert pd.Timestamp(dates[0]) >= data["S0"].index[250]


def test_blend_mapping():
    assert blend(40, None, 0.5) == 40
    assert blend(40, 0.5, 0.5) == 20
    assert blend(0, 0.75, 1.0) == 100
    assert blend(0, 0.1, 1.0) == -100


def test_backtest_fills_next_open_and_ml_strategies_run():
    data = {f"S{i}": make(i, n=650, edge=0.4) for i in range(10)}
    r = run_backtest(data, None, BTParams(strategy="sma_cross", slippage_bps=0))
    for t in r["trades"][:5]:
        d = pd.Timestamp(t["entry_date"])
        assert t["entry"] == pytest.approx(float(data[t["symbol"]].at[d, "open"]), abs=0.01)  # slipped() rounds to paise
    for st in ("ml", "blend"):
        res = run_backtest(data, None, BTParams(strategy=st))
        assert "metrics" in res and res["model_signals_from"]


def test_registry_round_trip(tmp_path):
    b = train_and_evaluate({f"S{i}": make(i, edge=0.5) for i in range(8)}, None)
    reg = Registry(tmp_path)
    reg.save(b)
    assert reg.active_version() == b.version
    loaded = reg.active()
    X = build_features(make(3)).dropna().iloc[-5:]
    assert np.allclose(loaded.predict(X), b.predict(X))
    reg.deactivate()
    assert reg.active() is None


def test_charges():
    assert 110 < charges("BUY", 100, 1000, "CNC") < 120
    assert 25 < charges("BUY", 100, 1000, "MIS") < 35


def test_prompts_exist():
    for n in ("analyst", "analyst_task", "bull", "bear", "portfolio_manager"):
        assert len(prompt(n)) > 40


def test_claude_desk_structured_decision():
    class Fake:
        def create(self, **kw):
            if kw.get("tool_choice"):
                return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={
                    "action": "BUY", "conviction": 0.7, "size_pct": 8, "horizon": "weeks", "rationale": "ok",
                    "stop_pct": 3, "target_pct": 9})])
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="report")])

    desk = DecisionDesk(DeskConfig())
    desk.client = SimpleNamespace(messages=Fake())
    c = Candidate("ITC", "NSE", "FMCG", 400.0, 55, "bullish", ["above 50-day average"], {"rsi14": 60}, ml_prob=0.62,
                  rule_score=50)
    d = desk.decide(c, lambda *a: None)
    assert (d.engine, d.action, d.size_pct, d.debate["bear"]) == ("claude", "BUY", 8, "report")


def test_rule_desk_without_key():
    desk = DecisionDesk(DeskConfig())
    c = Candidate("ITC", "NSE", "FMCG", 400.0, 55, "bullish", ["x"], {"atr_pct": 2})
    assert desk.decide(c, lambda *a: None).action == "BUY"
    c.held_qty, c.avg_price, c.score, c.stance = 10, 380.0, -30, "bearish"
    assert desk.decide(c, lambda *a: None).action == "SELL"
