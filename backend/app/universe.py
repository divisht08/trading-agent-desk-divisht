"""Which stocks the agent looks at, and their sectors.

Sectors come from the official NSE index constituent files on niftyindices.com
(columns: Company Name, Industry, Symbol, Series, ISIN Code). They are cached in
DATA_DIR. If the download fails, a small built-in seed is used; any seed symbol
that Kite no longer lists is silently dropped when matched to Kite instruments.

UNIVERSE_INDEX options:
  nifty50 | nifty100 | nifty200 | nifty500 | niftymidcap150 | niftysmallcap250
  all      -> every NSE equity Kite lists (sectors from Nifty 500 where known,
              otherwise "Other"); filtered by liquidity in the engine.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date

import httpx

from .config import settings

log = logging.getLogger(__name__)

INDEX_FILES = {
    "nifty50": "ind_nifty50list.csv",
    "nifty100": "ind_nifty100list.csv",
    "nifty200": "ind_nifty200list.csv",
    "nifty500": "ind_nifty500list.csv",
    "niftymidcap150": "ind_niftymidcap150list.csv",
    "niftysmallcap250": "ind_niftysmallcap250list.csv",
}
BASE = "https://www.niftyindices.com/IndexConstituent/"

# Fallback only. Refreshed from niftyindices.com whenever reachable.
SEED: list[tuple[str, str]] = [
    ("RELIANCE", "Oil Gas & Consumable Fuels"), ("ONGC", "Oil Gas & Consumable Fuels"),
    ("BPCL", "Oil Gas & Consumable Fuels"), ("COALINDIA", "Oil Gas & Consumable Fuels"),
    ("HDFCBANK", "Financial Services"), ("ICICIBANK", "Financial Services"),
    ("SBIN", "Financial Services"), ("KOTAKBANK", "Financial Services"),
    ("AXISBANK", "Financial Services"), ("BAJFINANCE", "Financial Services"),
    ("BAJAJFINSV", "Financial Services"), ("SHRIRAMFIN", "Financial Services"),
    ("SBILIFE", "Financial Services"), ("HDFCLIFE", "Financial Services"),
    ("TCS", "Information Technology"), ("INFY", "Information Technology"),
    ("HCLTECH", "Information Technology"), ("WIPRO", "Information Technology"),
    ("TECHM", "Information Technology"),
    ("ITC", "Fast Moving Consumer Goods"), ("HINDUNILVR", "Fast Moving Consumer Goods"),
    ("NESTLEIND", "Fast Moving Consumer Goods"), ("TATACONSUM", "Fast Moving Consumer Goods"),
    ("BRITANNIA", "Fast Moving Consumer Goods"),
    ("MARUTI", "Automobile and Auto Components"), ("M&M", "Automobile and Auto Components"),
    ("BAJAJ-AUTO", "Automobile and Auto Components"), ("EICHERMOT", "Automobile and Auto Components"),
    ("HEROMOTOCO", "Automobile and Auto Components"),
    ("SUNPHARMA", "Healthcare"), ("DRREDDY", "Healthcare"), ("CIPLA", "Healthcare"),
    ("APOLLOHOSP", "Healthcare"),
    ("LT", "Construction"), ("ULTRACEMCO", "Construction Materials"), ("GRASIM", "Construction Materials"),
    ("TATASTEEL", "Metals & Mining"), ("JSWSTEEL", "Metals & Mining"), ("HINDALCO", "Metals & Mining"),
    ("NTPC", "Power"), ("POWERGRID", "Power"),
    ("BHARTIARTL", "Telecommunication"),
    ("ASIANPAINT", "Consumer Durables"), ("TITAN", "Consumer Durables"),
    ("TRENT", "Consumer Services"),
    ("ADANIENT", "Metals & Mining"), ("ADANIPORTS", "Services"),
    ("BEL", "Capital Goods"),
]


@dataclass
class Stock:
    symbol: str
    exchange: str
    sector: str
    token: int
    name: str = ""

    @property
    def key(self) -> str:
        return f"{self.exchange}:{self.symbol}"


def _download(index: str) -> list[tuple[str, str]] | None:
    fname = INDEX_FILES[index]
    cache = settings.data_dir / f"{date.today():%Y%m}_{fname}"
    text = None
    if cache.exists():
        text = cache.read_text()
    else:
        try:
            r = httpx.get(BASE + fname, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            text = r.text
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            cache.write_text(text)
        except Exception as e:  # noqa: BLE001
            log.warning("Could not fetch %s from niftyindices.com: %s", fname, e)
            older = sorted(settings.data_dir.glob(f"*_{fname}"))
            if older:
                text = older[-1].read_text()
    if not text:
        return None
    rows = list(csv.DictReader(io.StringIO(text)))
    out = [(r["Symbol"].strip(), r["Industry"].strip()) for r in rows if r.get("Symbol")]
    return out or None


def sector_map(index: str) -> tuple[list[tuple[str, str]], str]:
    """Return ([(symbol, sector)], source_label)."""
    idx = index if index in INDEX_FILES else "nifty500"
    got = _download(idx)
    if got:
        return got, f"niftyindices.com {idx}"
    return SEED, "built-in seed (niftyindices.com unreachable)"


def build_universe(gateway) -> tuple[list[Stock], str]:
    """Match the sector list to Kite instruments so every stock is tradable on Kite."""
    index = settings.universe_index.lower()
    via_resolve = hasattr(gateway, "resolve")
    if index == "all" and via_resolve:
        index = "nifty500"  # the free MCP has no full instrument dump
    pairs, source = sector_map("nifty500" if index == "all" else index)
    if via_resolve and settings.universe_index.lower() == "all":
        source += " (UNIVERSE_INDEX=all needs DATA_MODE=kite; using Nifty 500)"
    sectors = dict(pairs)
    if getattr(gateway, "demo", False):
        gateway.set_symbols(pairs if index != "all" else pairs[:120])

    stocks: list[Stock] = []
    seen: set[str] = set()
    for exch in settings.exchanges:
        if via_resolve:
            wanted = [s for s, _ in pairs] + [s for s in settings.extra_symbols if s not in sectors]
            records = gateway.resolve([s for s in wanted if s not in seen], exch)
        else:
            records = gateway.instruments(exch)
        for ins in records:
            if ins.get("segment") != exch or ins.get("instrument_type") != "EQ":
                continue
            sym = ins["tradingsymbol"]
            if sym in seen:
                continue  # first exchange in EXCHANGES wins (NSE first = better liquidity)
            if index != "all" and sym not in sectors and sym not in settings.extra_symbols:
                continue
            seen.add(sym)
            stocks.append(Stock(sym, exch, sectors.get(sym, "Other"), int(ins["instrument_token"]),
                                ins.get("name") or sym))
    return stocks, source
