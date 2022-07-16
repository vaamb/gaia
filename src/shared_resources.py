from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers import SchedulerAlreadyRunningError


scheduler = BackgroundScheduler()


def start_scheduler():
    try:
        scheduler.start()
    except SchedulerAlreadyRunningError:
        # Bad practice
        pass
