"""TradingAgents-style decision desk on Claude.

Roles (after TauricResearch/TradingAgents, Apache-2.0):
  analyst            technical read + latest news (Claude web search, optional)
  bull / bear        argue each side
  portfolio manager  weighs both, applies a risk view, returns a structured decision

Prompts live in ./prompts/*.md so they can be edited without touching code.
Without an API key the desk falls back to a transparent rule set.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

PROMPTS = Path(__file__).parent / "prompts"
WEB_SEARCH_TOOL = os.getenv("WEB_SEARCH_TOOL_TYPE", "web_search_20250305")


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text().strip()


DECISION_TOOL = {
    "name": "submit_decision",
    "description": "Submit the final trading decision for this stock.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD", "AVOID"],
                       "description": "BUY opens/adds; SELL exits a held position (or opens an intraday "
                                      "short if allowed); HOLD keeps a held position; AVOID skips a non-held stock."},
            "conviction": {"type": "number", "minimum": 0, "maximum": 1},
            "size_pct": {"type": "number", "minimum": 0, "maximum": 100,
                         "description": "Percent of total portfolio equity to allocate for a BUY."},
            "stop_pct": {"type": "number", "description": "Stop distance in % from entry (0 if none)."},
            "target_pct": {"type": "number", "description": "Target in % from entry (0 if none)."},
            "horizon": {"type": "string", "enum": ["intraday", "days", "weeks", "months"]},
            "rationale": {"type": "string", "description": "Two to four sentences a human can audit."},
        },
        "required": ["action", "conviction", "size_pct", "horizon", "rationale"],
    },
}


@dataclass
class Prices:
    """US$ per million tokens, and per 1,000 web searches."""
    quick_in: float = 1.0
    quick_out: float = 5.0
    deep_in: float = 3.0
    deep_out: float = 15.0
    web_search_1k: float = 10.0


class Spend:
    """Daily Claude spend gate. `load`/`save` persist today's total (the backend keeps it in SQLite)."""

    def __init__(self, cap_usd: float, load: Callable[[], float], save: Callable[[float], None]):
        self.cap, self._load, self._save = cap_usd, load, save

    @property
    def today(self) -> float:
        return float(self._load() or 0.0)

    def allowed(self) -> bool:
        return self.cap <= 0 or self.today < self.cap

    def add(self, usd: float) -> None:
        self._save(round(self.today + usd, 6))


@dataclass
class DeskConfig:
    api_key: str = ""
    deep_model: str = "claude-sonnet-5-5"
    quick_model: str = "claude-haiku-5-5"
    use_web_news: bool = True
    allow_shorts: bool = False
    prices: Prices = field(default_factory=Prices)


@dataclass
class Candidate:
    symbol: str
    exchange: str
    sector: str
    ltp: float
    score: float
    stance: str
    reasons: list[str]
    features: dict
    held_qty: int = 0
    avg_price: float = 0.0
    memory: list[str] = field(default_factory=list)
    ml_prob: float | None = None
    rule_score: float | None = None


@dataclass
class Decision:
    action: str
    conviction: float
    size_pct: float
    horizon: str
    rationale: str
    stop_pct: float = 0.0
    target_pct: float = 0.0
    debate: dict = field(default_factory=dict)
    engine: str = "rules"


Emit = Callable[[str, str, dict | None], None]


def brief(c: Candidate) -> str:
    f = c.features
    pos = (f"Currently HELD: {c.held_qty} shares at avg ₹{c.avg_price:.2f} "
           f"({(c.ltp / c.avg_price - 1) * 100:+.1f}% unrealised)." if c.held_qty else "Not currently held.")
    mem = "\n".join(f"- {m}" for m in c.memory) or "- none yet"
    ml = (f"ML model: {c.ml_prob:.0%} probability that the 5-day return beats costs "
          f"(rule score {c.rule_score:+.0f}, blended score {c.score:+.0f}).\n" if c.ml_prob is not None else "")
    return (
        f"Stock: {c.symbol} ({c.exchange}), sector: {c.sector}. Last price ₹{c.ltp:.2f}.\n"
        f"Screen score {c.score:+.0f} on -100..+100 -> {c.stance}.\n{ml}"
        f"Signals: {'; '.join(c.reasons)}.\n"
        f"Key numbers: RSI {f.get('rsi14')}, 20d return {f.get('ret20')}%, 60d return {f.get('ret60')}%, "
        f"ATR {f.get('atr_pct')}% of price, avg turnover ₹{f.get('turnover_cr')} cr/day, "
        f"volume vs 20d avg {f.get('vol_ratio')}x.\n{pos}\n"
        f"Lessons from this desk's past calls on this stock:\n{mem}"
    )


class DecisionDesk:
    def __init__(self, cfg: DeskConfig, spend: Spend | None = None):
        self.cfg = cfg
        self.spend = spend
        self.client = None
        if cfg.api_key:
            from anthropic import Anthropic
            self.client = Anthropic(api_key=cfg.api_key)

    @property
    def engine(self) -> str:
        return "claude" if self.client else "rules"

    def _cost(self, msg, deep: bool) -> float:
        u = getattr(msg, "usage", None)
        if u is None:
            return 0.0
        p = self.cfg.prices
        rin, rout = (p.deep_in, p.deep_out) if deep else (p.quick_in, p.quick_out)
        tokens_in = (getattr(u, "input_tokens", 0) or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0) \
            + (getattr(u, "cache_read_input_tokens", 0) or 0)
        cost = tokens_in * rin / 1e6 + (getattr(u, "output_tokens", 0) or 0) * rout / 1e6
        stu = getattr(u, "server_tool_use", None)
        searches = (getattr(stu, "web_search_requests", 0) or 0) if stu else 0
        return cost + searches * p.web_search_1k / 1000

    def _charge(self, msg, deep: bool) -> None:
        if self.spend:
            self.spend.add(self._cost(msg, deep))

    def decide(self, c: Candidate, emit: Emit) -> Decision:
        if not self.client:
            return self._rules(c)
        if self.spend and not self.spend.allowed():
            d = self._rules(c)
            d.engine = "rules (Claude budget reached)"
            return d
        try:
            return self._claude(c, emit)
        except Exception as e:  # noqa: BLE001 - one stock must not kill the cycle
            log.exception("Claude desk failed for %s", c.symbol)
            emit("warn", f"{c.symbol}: Claude call failed ({e.__class__.__name__}); used the rule set", None)
            return self._rules(c)

    # -- Claude --------------------------------------------------------------
    def _text(self, model: str, system: str, user: str, web: bool = False, max_tokens: int = 900) -> str:
        kw: dict = {}
        if web:
            kw["tools"] = [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": 3}]
        msg = self.client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                          messages=[{"role": "user", "content": user}], **kw)
        self._charge(msg, deep=model == self.cfg.deep_model)
        return "\n".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()

    def _claude(self, c: Candidate, emit: Emit) -> Decision:
        b = brief(c)
        emit("info", f"{c.symbol}: analyst reading signals" + (" and today's news" if self.cfg.use_web_news else ""), None)
        analyst = self._text(self.cfg.quick_model, prompt("analyst"), f"{b}\n\n{prompt('analyst_task')}",
                             web=self.cfg.use_web_news)
        emit("info", f"{c.symbol}: bull and bear researchers debating", None)
        bull = self._text(self.cfg.quick_model, prompt("bull"), f"{b}\n\nAnalyst report:\n{analyst}")
        bear = self._text(self.cfg.quick_model, prompt("bear"),
                          f"{b}\n\nAnalyst report:\n{analyst}\n\nBull case:\n{bull}")
        emit("info", f"{c.symbol}: portfolio manager deciding", None)
        shorts = ("Intraday shorts are allowed." if self.cfg.allow_shorts
                  else "Shorting is NOT allowed: SELL only exits a held position.")
        msg = self.client.messages.create(
            model=self.cfg.deep_model, max_tokens=800,
            system=prompt("portfolio_manager").replace("{shorts}", shorts),
            messages=[{"role": "user", "content":
                       f"{b}\n\nAnalyst:\n{analyst}\n\nBull:\n{bull}\n\nBear:\n{bear}\n\nSubmit your decision."}],
            tools=[DECISION_TOOL], tool_choice={"type": "tool", "name": "submit_decision"},
        )
        self._charge(msg, deep=True)
        d = next(x.input for x in msg.content if getattr(x, "type", "") == "tool_use")
        return Decision(action=d["action"], conviction=float(d.get("conviction", 0)),
                        size_pct=float(d.get("size_pct", 0)), horizon=d.get("horizon", "days"),
                        rationale=d.get("rationale", ""), stop_pct=float(d.get("stop_pct") or 0),
                        target_pct=float(d.get("target_pct") or 0),
                        debate={"analyst": analyst, "bull": bull, "bear": bear}, engine="claude")

    # -- rules ---------------------------------------------------------------
    def _rules(self, c: Candidate) -> Decision:
        conv = round(min(1.0, abs(c.score) / 80), 2)
        ml = f"; model {c.ml_prob:.0%}" if c.ml_prob is not None else ""
        deb = {"analyst": "; ".join(c.reasons) + ml,
               "bull": "Bull view: " + "; ".join(c.reasons[:4]),
               "bear": f"Bear view: ATR {c.features.get('atr_pct')}% of price; RSI {c.features.get('rsi14')}"}
        atr = c.features.get("atr_pct") or 2
        if c.held_qty > 0:
            pnl = (c.ltp / c.avg_price - 1) * 100 if c.avg_price else 0
            if c.stance == "bearish" or c.score < 0:
                return Decision("SELL", conv, 0, "days",
                                f"Exit: screen turned {c.stance} (score {c.score:+.0f}); position {pnl:+.1f}%.", debate=deb)
            return Decision("HOLD", conv, 0, "weeks", f"Trend intact (score {c.score:+.0f}); holding at {pnl:+.1f}%.",
                            debate=deb)
        if c.held_qty < 0:
            if c.score > 0:
                return Decision("BUY", conv, 0, "intraday", "Cover short: screen no longer bearish.", debate=deb)
            return Decision("HOLD", conv, 0, "intraday", "Short still valid.", debate=deb)
        if c.score >= 40:
            return Decision("BUY", conv, round(4 + 8 * conv, 1), "weeks",
                            f"Strong screen ({c.score:+.0f}){ml}: {', '.join(c.reasons[:3])}.",
                            stop_pct=round(2 * atr, 1), target_pct=round(4 * atr, 1), debate=deb)
        if c.score <= -40 and self.cfg.allow_shorts:
            return Decision("SELL", conv, round(3 + 5 * conv, 1), "intraday",
                            f"Weak screen ({c.score:+.0f}) - intraday short.", stop_pct=round(atr, 1), debate=deb)
        return Decision("AVOID", conv, 0, "days", f"Not strong enough ({c.score:+.0f}).", debate=deb)
