import datetime
import io
import pytz

from apscheduler.schedulers.background import BackgroundScheduler
import numpy as np

from config import Config
from engine.config_parser import localTZ
from engine.subroutine_template import subroutineTemplate


class gaiaHealth(subroutineTemplate):
    NAME = "health"

    def __init__(self, ecosystem=None, engine=None) -> None:
        super().__init__(ecosystem=ecosystem, engine=engine)

        self._timezone = localTZ
        self._health_data = {}
        self._scheduler = BackgroundScheduler()
        self._imageIO = io.BytesIO

        self._finish__init__()

    def _start_scheduler(self):
        h, m = Config.HEALTH_LOGGING_TIME.split("h")
        self._scheduler.add_job(self.health_routine, trigger="cron",
                                hour=h, minute=m, misfire_grace_time=15 * 60,
                                id="health")
        self._scheduler.start()

    def _stop_scheduler(self):
        self._logger.info("Closing the tasks scheduler")
        self._scheduler.remove_job("health")
#        self._scheduler.shutdown()
        self._logger.info("The tasks scheduler was closed properly")

    def health_routine(self):
        if self._engine:
            light = self._engine.subroutines["light"].status
            if light:
                light_mode = self._engine.subroutines["light"].mode or "automatic"
                light_status = self._engine.subroutines["light"].light_status or False
                self._engine.set_light_on()

        try:
            self._logger.info(f"Taking picture of {self._ecosystem}")
            self.take_picture()
            self._logger.debug(
                f"Picture of {self._ecosystem} successfully taken")
        except Exception as e:
            self._logger.error(f"Failing to take picture of {self._ecosystem}. "
                               f"ERROR msg: {e}")

        if self._engine:
            if light:
                if light_mode == "automatic":
                    self._engine.subroutines["light"].set_light_auto()
                else:
                    if light_status:
                        self._engine.subroutines["light"].set_light_on()
                    else:
                        self._engine.subroutines["light"].set_light_off()

        self.analyse_image()

    def _start(self):
        pass

    def _stop(self):
        self._health_data = {}
        pass

    def take_picture(self):
        pass

    def analyse_image(self):
        self._logger.info(f"Starting analysis of {self._ecosystem} image")
        # If got an image, analyse it
        if self._imageIO.getbuffer().nbytes:
            import random
            green = random.randrange(12000, 1500000, 1000)
            necrosis = random.uniform(5, 55)
            health_index = random.uniform(70, 97)
            self._health_data = {
                self.ecosystem: {
                    "datetime": datetime.datetime.now().replace(microsecond=0).
                        astimezone(pytz.timezone(self.timezone)),
                    "green": green,
                    "necrosis": round(necrosis, 2),
                    "index": round(health_index, 2)
                }
            }
            self._logger.info(f"{self._ecosystem} picture successfully analysed, "
                             f"indexes computed")
        else:
            # TODO: change Exception
            raise Exception

    @property
    def health_data(self):
        return self._health_data
