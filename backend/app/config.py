"""Runtime configuration from environment variables (see .env.example at the repo root)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float | None) -> float | None:
    v = os.getenv(name)
    return default if v is None or v.strip() == "" else float(v)


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass
class Settings:
    # data source:
    #   "kite_mcp" - free data through Zerodha's hosted Kite MCP (default)
    #   "kite"     - Kite Connect API data (paid Connect plan, ₹500/month)
    #   "demo"     - synthetic prices, clearly labelled
    data_mode: str = field(default_factory=lambda: os.getenv("DATA_MODE", "kite_mcp"))
    kite_mcp_url: str = field(default_factory=lambda: os.getenv("KITE_MCP_URL", "https://mcp.kite.trade/mcp"))

    # Kite Connect. With DATA_MODE=kite_mcp these are only needed for Automatic mode (orders),
    # and the free Personal plan is enough.
    kite_api_key: str = field(default_factory=lambda: os.getenv("KITE_API_KEY", ""))
    kite_api_secret: str = field(default_factory=lambda: os.getenv("KITE_API_SECRET", ""))
    public_url: str = field(default_factory=lambda: os.getenv("PUBLIC_URL", "http://localhost:8000"))

    # Trading modes. Test = simulated orders on live prices. Automatic = real orders.
    # Automatic can only be switched on when this is true AND after password + authenticator code.
    live_trading_enabled: bool = field(default_factory=lambda: _bool("LIVE_TRADING_ENABLED"))
    live_capital: float | None = field(default_factory=lambda: _float("LIVE_CAPITAL", None))
    auto_reverify_daily: bool = field(default_factory=lambda: _bool("AUTO_REVERIFY_DAILY"))
    paper_capital: float = field(default_factory=lambda: _float("PAPER_CAPITAL", 100000.0) or 100000.0)

    # universe
    universe_index: str = field(default_factory=lambda: os.getenv("UNIVERSE_INDEX", "nifty100"))
    exchanges: list[str] = field(default_factory=lambda: _list("EXCHANGES", "NSE"))
    extra_symbols: list[str] = field(default_factory=lambda: _list("EXTRA_SYMBOLS"))

    # Claude desk
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    deep_model: str = field(default_factory=lambda: os.getenv("DEEP_MODEL", "claude-sonnet-5-5"))
    quick_model: str = field(default_factory=lambda: os.getenv("QUICK_MODEL", "claude-haiku-5-5"))
    use_web_news: bool = field(default_factory=lambda: _bool("USE_WEB_NEWS", True))
    candidates_per_cycle: int = field(default_factory=lambda: int(os.getenv("CANDIDATES_PER_CYCLE", "5")))
    # Claude spend control: a stock is re-debated only when its last debate is older than
    # DEBATE_REFRESH_MIN or its score moved by DEBATE_SCORE_DELTA; spend stops at the daily cap.
    debate_refresh_min: int = field(default_factory=lambda: int(os.getenv("DEBATE_REFRESH_MIN", "180")))
    debate_score_delta: float = field(default_factory=lambda: _float("DEBATE_SCORE_DELTA", 15.0) or 15.0)
    claude_daily_budget_usd: float = field(default_factory=lambda: _float("CLAUDE_DAILY_BUDGET_USD", 2.0) or 0.0)
    # $ per million tokens and per 1,000 web searches. Set these to your actual prices.
    price_quick_in: float = field(default_factory=lambda: _float("PRICE_QUICK_IN", 1.0) or 0.0)
    price_quick_out: float = field(default_factory=lambda: _float("PRICE_QUICK_OUT", 5.0) or 0.0)
    price_deep_in: float = field(default_factory=lambda: _float("PRICE_DEEP_IN", 3.0) or 0.0)
    price_deep_out: float = field(default_factory=lambda: _float("PRICE_DEEP_OUT", 15.0) or 0.0)
    price_web_search_1k: float = field(default_factory=lambda: _float("PRICE_WEB_SEARCH_1K", 10.0) or 0.0)

    # ML model
    ml_weight: float = field(default_factory=lambda: _float("ML_WEIGHT", 0.4) or 0.0)
    ml_auto_retrain: bool = field(default_factory=lambda: _bool("ML_AUTO_RETRAIN", True))
    ml_min_auc: float = field(default_factory=lambda: _float("ML_MIN_AUC", 0.52) or 0.52)
    ml_history_days: int = field(default_factory=lambda: int(os.getenv("ML_HISTORY_DAYS", "1200")))

    # schedule (IST)
    cycle_minutes: int = field(default_factory=lambda: int(os.getenv("CYCLE_MINUTES", "15")))
    market_open: str = field(default_factory=lambda: os.getenv("MARKET_OPEN", "09:20"))
    market_close: str = field(default_factory=lambda: os.getenv("MARKET_CLOSE", "15:15"))
    login_reminder: str = field(default_factory=lambda: os.getenv("LOGIN_REMINDER", "08:45"))
    retrain_time: str = field(default_factory=lambda: os.getenv("RETRAIN_TIME", "fri 15:40"))
    holidays: list[str] = field(default_factory=lambda: _list("NSE_HOLIDAYS"))
    scheduler_enabled: bool = field(default_factory=lambda: _bool("SCHEDULER_ENABLED", True))

    # execution model
    product: str = field(default_factory=lambda: os.getenv("PRODUCT", "CNC"))
    allow_intraday_shorts: bool = field(default_factory=lambda: _bool("ALLOW_INTRADAY_SHORTS"))
    slippage_bps: float = field(default_factory=lambda: _float("SLIPPAGE_BPS", 5.0) or 0.0)
    max_positions: int = field(default_factory=lambda: int(os.getenv("MAX_POSITIONS", "10")))

    # optional risk limits (blank = off)
    max_position_pct: float | None = field(default_factory=lambda: _float("MAX_POSITION_PCT", None))
    daily_loss_limit_pct: float | None = field(default_factory=lambda: _float("DAILY_LOSS_LIMIT_PCT", None))
    stop_loss_pct: float | None = field(default_factory=lambda: _float("STOP_LOSS_PCT", None))

    # phone alerts
    ntfy_topic: str = field(default_factory=lambda: os.getenv("NTFY_TOPIC", ""))
    ntfy_server: str = field(default_factory=lambda: os.getenv("NTFY_SERVER", "https://ntfy.sh"))
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # security
    dashboard_password: str = field(default_factory=lambda: os.getenv("DASHBOARD_PASSWORD", ""))
    secret_key: str = field(default_factory=lambda: os.getenv("SECRET_KEY", "change-me"))
    totp_issuer: str = field(default_factory=lambda: os.getenv("TOTP_ISSUER", "Agent Desk"))

    # storage and frontend
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "./data")))
    frontend_dist: Path = field(default_factory=lambda: Path(
        os.getenv("FRONTEND_DIST", str(Path(__file__).resolve().parents[2] / "frontend" / "dist"))))

    @property
    def db_path(self) -> Path:
        return self.data_dir / ("demo.sqlite3" if self.is_demo else "trading.sqlite3")

    @property
    def model_dir(self) -> Path:
        return self.data_dir / ("models-demo" if self.is_demo else "models")

    @property
    def is_demo(self) -> bool:
        return self.data_mode == "demo"


settings = Settings()
