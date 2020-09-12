#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
import datetime
import pytz


class gaiaHealth():
    def __init__(self, completeConfigObject):
        self.config = completeConfigObject
        self.ecosystem = self.config.name
        self.name = "health"
        self.logger = logging.getLogger("eng.{}.Health".format(self.ecosystem))
        self.logger.debug(f"Initializing gaiaHealth for {self.ecosystem}")
        self.timezone = self.config.local_timezone
        self.health_data = {}
    
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
                "datetime": datetime.datetime.now().replace(microsecond = 0).
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