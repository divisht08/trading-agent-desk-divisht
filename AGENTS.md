# Working on Agent Desk

Guide for anyone (human or AI) changing this repo. Read this before editing.

## Layout

| Path | What lives there | Language |
|---|---|---|
| `backend/app/` | FastAPI API, trading engine (the cycle), Kite gateway, brokers and books, scheduler, security (password + TOTP), notifications | Python 3.12 |
| `ml/deskml/` | Rule signals, ML features, model training and registry, backtester, Zerodha charges, the Claude decision desk | Python 3.12 |
| `ml/deskml/agents/prompts/` | Prompts for analyst, bull, bear, portfolio manager. Edit these to change desk behaviour | Markdown |
| `frontend/src/` | React + TypeScript dashboard (Vite). `lib/api.ts` is the typed API client, `lib/desk.tsx` the shared state and live stream | TS |
| `.github/workflows/ci.yml` | Tests for ml, backend and frontend, plus a Docker build | YAML |

`backend` depends on `ml` (`pip install -e ml`). `ml` never imports `backend`.

## Commands

```bash
make install          # pip install -e ml -e backend[dev]; npm ci
make test             # all three test suites + typecheck
make demo             # full app on synthetic prices, password "demo", http://localhost:8000
make dev-api          # API on :8000 with reload
make dev-web          # React dev server on :5173, proxies /api to :8000
```

## Rules that must not break

1. **Real orders only in Automatic mode.** `Engine.book` is `live` only when `trading_mode == "automatic"`.
   The only way to set that is `Engine.arm_automatic()`, which requires `LIVE_TRADING_ENABLED`,
   `LIVE_CAPITAL`, a Kite session, non-demo data, and `TwoStep.check(password, code)`. Keep it that way;
   add tests in `backend/tests/test_backend.py` for any change near it.
2. **The live book holds only what the agent bought.** Never sell more than the live book's quantity.
   `LiveBroker.reconcile()` reports gaps and never trades. The fix-position endpoint may only lower quantities.
3. **No look-ahead in ML or backtests.** Features at bar t use data up to t. Labels look HORIZON bars ahead,
   so training must purge HORIZON bars before any evaluation or prediction date. Backtests fill at the
   next bar's open. `ml/tests/test_ml.py` checks this; extend it when touching features or the model.
4. **Demo data is always labelled.** `DATA_MODE=demo` can never place live orders, and the UI shows a banner.
5. **Never fabricate prices.** If Kite data is missing, the cycle stops at that stage and says so.
6. **Charges come from zerodha.com/charges.** Update `ml/deskml/costs.py` (with the date checked) when they change.

## Where things happen

- One trading cycle: `backend/app/engine.py` `Engine._run` (stages: session, universe, market, signals,
  debate, risk, execute, reconcile). Each stage writes events with `store.event(...)`; the dashboard streams them.
- Scoring: `deskml.signals.score_frame` (rules) blended with `deskml.model` probability via `blend()`,
  weight `ML_WEIGHT`.
- Model training: `Engine.train_model` → `deskml.model.train_and_evaluate` → `Registry` in `DATA_DIR/models`.
  Auto-activates only if holdout AUC ≥ `ML_MIN_AUC` and not worse than the active model.
- Schedules: `backend/app/scheduler.py` (cycles in market hours, 08:45 login reminder, 15:15 intraday
  square-off, weekly retrain).

## Style

- Python: type hints, small functions, no new dependencies without a reason. Errors that a user can act on
  become clear messages (they show in the dashboard).
- Frontend: plain CSS tokens in `styles.css` (light and dark), no UI kit. Bullish/bearish colour always
  comes with ▲/▼ and a number. Copy is sentence case and says what happens.
- Commits: one area per commit where possible (`backend: ...`, `ml: ...`, `frontend: ...`).
