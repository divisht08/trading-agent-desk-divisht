"""Download history via Kite Connect for backtesting.

  data/daily/SYMBOL.csv   ~8 years of daily bars (+ NIFTY50.csv benchmark)
  data/5m/SYMBOL.csv      last ~400 days of 5-minute bars (fetched in 90-day chunks)

Usage: KITE_API_KEY=... KITE_ACCESS_TOKEN=... python -m agent.fetch_history
(Access token: open the running service's /login once, or use kite.generate_session.)
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from kiteconnect import KiteConnect

from .config import Config

NIFTY50_TOKEN = 256265


def pull(k, token, frm, to, interval, chunk_days):
    out, a = [], frm
    while a < to:
        b = min(a + timedelta(days=chunk_days), to)
        out += k.historical_data(token, a, b, interval)
        time.sleep(0.4)                       # Kite historical limit ~3 req/s
        a = b + timedelta(days=1)
    df = pd.DataFrame(out)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df[["date", "open", "high", "low", "close", "volume"]].drop_duplicates("date")


def main():
    cfg = Config.load()
    k = KiteConnect(api_key=os.environ["KITE_API_KEY"])
    k.set_access_token(os.environ["KITE_ACCESS_TOKEN"])
    tokens = {i["tradingsymbol"]: i["instrument_token"] for i in k.instruments("NSE") if i["segment"] == "NSE"}
    now = datetime.now()
    for sub in ("daily", "5m"):
        Path(f"data/{sub}").mkdir(parents=True, exist_ok=True)
    pull(k, NIFTY50_TOKEN, now - timedelta(days=365 * 8), now, "day", 1900).to_csv("data/daily/NIFTY50.csv", index=False)
    for s in cfg["universe"]:
        if s not in tokens:
            print("missing", s)
            continue
        pull(k, tokens[s], now - timedelta(days=365 * 8), now, "day", 1900).to_csv(f"data/daily/{s}.csv", index=False)
        pull(k, tokens[s], now - timedelta(days=400), now, "5minute", 90).to_csv(f"data/5m/{s}.csv", index=False)
        print("saved", s)


if __name__ == "__main__":
    main()
