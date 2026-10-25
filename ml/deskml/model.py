"""Gradient-boosted classifier: P(5-day forward return beats costs).

Training is time-aware:
- evaluation uses a later holdout window, with a HORIZON-day gap (purge) so
  no training label overlaps the holdout period;
- walk_forward() re-trains on a rolling schedule and only ever predicts dates
  after the last label it trained on, so backtests of the model are honest.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score

from .features import FEATURES, HORIZON, panel

log = logging.getLogger(__name__)


def make_estimator(seed: int = 7) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=250, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=150,
        l2_regularization=1.0, early_stopping=False, random_state=seed,
    )


@dataclass
class ModelBundle:
    version: str
    created: str
    trained_until: str
    n_train: int
    metrics: dict
    importance: list[dict] = field(default_factory=list)
    features: list[str] = field(default_factory=lambda: list(FEATURES))
    horizon: int = HORIZON
    model: object = None

    def meta(self) -> dict:
        d = asdict(self)
        d.pop("model")
        return d

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(X[self.features])[:, 1]


def _eval(y: np.ndarray, p: np.ndarray, fwd: np.ndarray) -> dict:
    out = {"n": int(len(y)), "base_rate": round(float(np.mean(y)), 4)}
    out["auc"] = round(float(roc_auc_score(y, p)), 4) if len(set(y)) > 1 else None
    k = max(1, len(p) // 10)
    top = np.argsort(-p)[:k]
    out["top_decile_hit_rate"] = round(float(np.mean(y[top])), 4)
    out["top_decile_avg_fwd_pct"] = round(float(np.nanmean(fwd[top]) * 100), 3)
    out["all_avg_fwd_pct"] = round(float(np.nanmean(fwd) * 100), 3)
    out["lift"] = round(out["top_decile_hit_rate"] / out["base_rate"], 3) if out["base_rate"] else None
    return out


def train_and_evaluate(data: dict[str, pd.DataFrame], bench: pd.Series | None, holdout_days: int = 120,
                       seed: int = 7) -> ModelBundle:
    P = panel(data, bench).dropna(subset=["y"])
    if P.empty:
        raise ValueError("No labelled rows - need at least ~130 daily bars per symbol")
    dates = np.sort(P["date"].unique())
    if len(dates) < holdout_days + 200:
        holdout_days = max(20, len(dates) // 4)
    cutoff = dates[-holdout_days]
    purge_end = dates[max(0, len(dates) - holdout_days - HORIZON)]
    train = P[P["date"] < purge_end]
    test = P[P["date"] >= cutoff]
    est = make_estimator(seed).fit(train[FEATURES], train["y"].astype(int))
    p = est.predict_proba(test[FEATURES])[:, 1]
    metrics = {"holdout": _eval(test["y"].to_numpy(), p, test["fwd"].to_numpy()),
               "holdout_from": str(pd.Timestamp(cutoff).date()),
               "train_rows": int(len(train)), "symbols": int(P["symbol"].nunique())}

    sample = test.sample(min(len(test), 4000), random_state=seed)
    imp = []
    if len(sample) > 50 and sample["y"].nunique() > 1:
        pi = permutation_importance(est, sample[FEATURES], sample["y"].astype(int), scoring="roc_auc",
                                    n_repeats=3, random_state=seed)
        imp = sorted(({"feature": f, "importance": round(float(m), 5)}
                      for f, m in zip(FEATURES, pi.importances_mean)), key=lambda x: -x["importance"])

    final = make_estimator(seed).fit(P[FEATURES], P["y"].astype(int))  # all labelled history for live use
    now = datetime.now()
    return ModelBundle(version=now.strftime("%Y%m%d-%H%M%S"), created=now.isoformat(timespec="seconds"),
                       trained_until=str(pd.Timestamp(dates[-1]).date()), n_train=int(len(P)),
                       metrics=metrics, importance=imp, model=final)


def walk_forward(data: dict[str, pd.DataFrame], bench: pd.Series | None, retrain_every: int = 63,
                 min_train_days: int = 250, seed: int = 7) -> pd.DataFrame:
    """Out-of-sample probabilities for every (date, symbol), re-training every `retrain_every` days.

    A model used on date d was trained only on rows whose label was known before d
    (row date <= d - HORIZON - 1 bars).
    """
    P = panel(data, bench)
    if P.empty:
        return pd.DataFrame(columns=["date", "symbol", "prob"])
    dates = np.sort(P["date"].unique())
    out = []
    for i in range(min_train_days, len(dates), retrain_every):
        last_known = dates[i - HORIZON - 1]
        tr = P[(P["date"] <= last_known) & P["y"].notna()]
        if len(tr) < 500 or tr["y"].nunique() < 2:
            continue
        est = make_estimator(seed).fit(tr[FEATURES], tr["y"].astype(int))
        mask = P["date"] >= dates[i]
        if i + retrain_every < len(dates):
            mask &= P["date"] < dates[i + retrain_every]
        block = P[mask]
        if block.empty:
            continue
        out.append(pd.DataFrame({"date": block["date"].to_numpy(), "symbol": block["symbol"].to_numpy(),
                                 "prob": est.predict_proba(block[FEATURES])[:, 1]}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["date", "symbol", "prob"])


def blend(rule_score: float, prob: float | None, weight: float) -> float:
    """Combine the rule score (-100..100) with the model probability.

    prob 0.5 maps to 0, 0.75 to +100, 0.25 to -100 (clipped). weight is the model's share.
    """
    if prob is None or weight <= 0:
        return float(rule_score)
    ml_score = float(np.clip((prob - 0.5) * 400, -100, 100))
    return round((1 - weight) * rule_score + weight * ml_score, 1)


class Registry:
    """Models saved as <dir>/model-<version>.joblib with a JSON sidecar; active.json picks one."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache: tuple[str, ModelBundle] | None = None

    def save(self, b: ModelBundle, activate: bool = True) -> str:
        joblib.dump(b.model, self.root / f"model-{b.version}.joblib")
        (self.root / f"model-{b.version}.json").write_text(json.dumps(b.meta(), indent=2))
        if activate:
            self.activate(b.version)
        return b.version

    def list(self) -> list[dict]:
        act = self.active_version()
        out = []
        for p in sorted(self.root.glob("model-*.json"), reverse=True):
            m = json.loads(p.read_text())
            m["active"] = m["version"] == act
            out.append(m)
        return out

    def activate(self, version: str) -> None:
        if not (self.root / f"model-{version}.joblib").exists():
            raise FileNotFoundError(version)
        (self.root / "active.json").write_text(json.dumps({"version": version}))
        self._cache = None

    def deactivate(self) -> None:
        (self.root / "active.json").unlink(missing_ok=True)
        self._cache = None

    def active_version(self) -> str | None:
        p = self.root / "active.json"
        return json.loads(p.read_text())["version"] if p.exists() else None

    def active(self) -> ModelBundle | None:
        v = self.active_version()
        if not v:
            return None
        if self._cache and self._cache[0] == v:
            return self._cache[1]
        meta = json.loads((self.root / f"model-{v}.json").read_text())
        b = ModelBundle(**meta, model=joblib.load(self.root / f"model-{v}.joblib"))
        self._cache = (v, b)
        return b
