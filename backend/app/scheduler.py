"""Market-hours scheduler (IST). Runs inside the app process."""
from __future__ import annotations

import logging
from datetime import time as dtime

from apscheduler.schedulers.background import BackgroundScheduler

from .config import settings
from .store import now_ist

log = logging.getLogger(__name__)


def _hm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def trading_day() -> bool:
    n = now_ist()
    return n.weekday() < 5 and n.date().isoformat() not in settings.holidays


def in_session() -> bool:
    n = now_ist()
    return trading_day() and _hm(settings.market_open) <= n.time() <= _hm(settings.market_close)


def start(engine) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="Asia/Kolkata")

    def cycle():
        if in_session():
            engine.run_cycle("schedule")

    def login_reminder():
        if trading_day() and not engine.gw.is_ready():
            engine.send_login_to_phone("Market opens soon and Kite needs today's login")

    def close_out():
        if trading_day():
            engine.square_off_intraday()
            engine.snapshot_equity(engine.book)

    def retrain():
        if settings.ml_auto_retrain and trading_day():
            engine.train_model("schedule")

    lh, lm = settings.login_reminder.split(":")
    ch, cm = settings.market_close.split(":")
    sched.add_job(cycle, "interval", minutes=settings.cycle_minutes, id="cycle", max_instances=1, coalesce=True)
    sched.add_job(login_reminder, "cron", day_of_week="mon-fri", hour=int(lh), minute=int(lm), id="login")
    sched.add_job(close_out, "cron", day_of_week="mon-fri", hour=int(ch), minute=int(cm), id="close")
    rday, rtime = settings.retrain_time.split()
    rh, rm = rtime.split(":")
    sched.add_job(retrain, "cron", day_of_week=rday, hour=int(rh), minute=int(rm), id="retrain")
    sched.start()
    log.info("Scheduler started: every %s min between %s and %s IST", settings.cycle_minutes,
             settings.market_open, settings.market_close)
    return sched
