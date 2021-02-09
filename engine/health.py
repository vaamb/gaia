import datetime
import logging
import pytz

from engine.config_parser import configWatchdog, getConfig, localTZ



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
        self.logger.info("Closing the tasks scheduler")
        self._scheduler.remove_job("health")
#        self._scheduler.shutdown()
        self.logger.info("The tasks scheduler was closed properly")

    def health_routine(self):
        if self._engine:
            light = self._engine.subroutines["light"].status
            if light:
                light_mode = self._engine.subroutines["light"].mode or "automatic"
                light_status = self._engine.subroutines["light"].light_status or False
                self._engine.set_light_on()

        self.take_picture()

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
        self.logger.info("Taking picture of {}".format(self.ecosystem))
        self.logger.info("Picture of {} successfully taken".format(self.ecosystem))
        pass

    def image_analysis(self):
        self.logger.info("Starting analysis of {} image".format(self.ecosystem))
        import random
        green = random.randrange(12000, 1500000, 1000)
        necrosis = random.uniform(5, 55)
        health_index = random.uniform(70, 97)
        self.health_data = {
            self.ecosystem: {
                "datetime": datetime.datetime.now().replace(microsecond=0).
                    astimezone(pytz.timezone(self.timezone)),
                "green": green,
                "necrosis": round(necrosis, 2),
                "index": round(health_index, 2)
            }
        }
        self.logger.info("{} picture successfully analysed, indexes computed".format(self.ecosystem))

    @property
    def health_data(self):
        return self._health_data
