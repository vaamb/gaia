from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers import SchedulerAlreadyRunningError


_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()
    return _scheduler


def start_scheduler():
    scheduler = get_scheduler()
    try:
        scheduler.start()
    except SchedulerAlreadyRunningError:
        # Bad practice
        pass
