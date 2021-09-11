from concurrent.futures import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers import SchedulerAlreadyRunningError


scheduler = BackgroundScheduler()
thread_pool = ThreadPoolExecutor(max_workers=5)


def start_scheduler():
    try:
        scheduler.start()
    except SchedulerAlreadyRunningError:
        # Bad practice
        pass
