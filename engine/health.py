import datetime
import logging
import pytz

from engine.config_parser import configWatchdog, getConfig, localTZ


class gaiaHealth:
    NAME = "health"

    def __init__(self, ecosystem):
        configWatchdog.start()
        self.config = getConfig(ecosystem)
        self.ecosystem = self.config.name
        self.logger = logging.getLogger("eng.{}.Health".format(self.ecosystem))
        self.logger.debug(f"Initializing gaiaHealth for {self.ecosystem}")
        self.timezone = localTZ
        self.health_data = None

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

    def get_health_data(self):
        return self.health_data

    def stop(self):
        pass
