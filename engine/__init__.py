#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""add an option for logging level, which will modify the imported LOGGING_CONFIG dict"""

import logging
import logging.config

from threading import Thread
from datetime import date, datetime
import json

from .config import gaiaConfig, LOGGING_CONFIG
from .light import gaiaLight
from .sensors import gaiaSensors
from .health import gaiaHealth
from .climate import gaiaClimate

logging.config.dictConfig(LOGGING_CONFIG)

class datetimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()

class gaiaEngine(Thread):
    """
    gaiaEngine is the module that controls all the subprocesses related to environment control
    such as gaiaLight, gaiaSensors, gaiaHealth ...
    It was adapted to a thread in the case where multiple instances of gaiaEngine are run on the
    same computer.
    """
    def __init__(self, ecosystem):
        super(gaiaEngine, self).__init__()
        self.ecosystem = ecosystem
        self.logger = logging.getLogger("eng.{}".format(self.ecosystem))
        self.config = gaiaConfig(self.ecosystem)
        self.ecosystem_id = self.config.name_to_id_dict[self.ecosystem]

    def run(self):
        self.logger.info("Starting gaiaEngine for {}".format(self.ecosystem))

        #Start the submodules as independent threads as they all are I/O bound
        threads = []
        for func in [self.load_light, self.load_sensors, self.load_health]:
            t = Thread(target = func, args=())
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

        self.logger.info("gaiaEngine successfully started for {}".format(self.ecosystem))

    def load_light(self):
        self.logger.info("Loading gaiaLight for {}".format(self.ecosystem))
        self.light = gaiaLight(self.ecosystem)
        self.logger.info("gaiaLight successfully loaded for {}".format(self.ecosystem))

    def load_sensors(self):
        self.logger.info("Loading gaiaSensors for {}".format(self.ecosystem))
        self.sensors = gaiaSensors(self.ecosystem)
        self.logger.info("gaiaSensors successfully loaded for {}".format(self.ecosystem))

    def load_health(self):
        self.logger.info("Loading gaiaHealth for {}".format(self.ecosystem))
        self.health = gaiaHealth(self.ecosystem)
        self.logger.info("gaiaHealth successfully loaded for {}".format(self.ecosystem))

    """Plant health part"""
    def health_routine(self):
        mode = self.light_mode
        status = self.light_status
        self.set_light_on()
        self.health.take_picture()
        if mode == "automatic":
            self.set_light_auto()
        else:
            if status:
                self.set_light_on()
            else:
                self.set_light_off()
        self.health.image_analysis()

    """API calls"""
    #Wrap the data into a json and add an ecosystem signature
    def wrapped_json(self, data):
        wrapped_data = {self.ecosystem_id: data}
        return datetimeEncoder().encode(wrapped_data)

    #Configuration info
    def environmental_sensors(self):
        return self.wrapped_json(self.config.get_sensor_list("environment"))

    def plant_sensors(self):
        return self.wrapped_json(self.config.get_sensor_list("plant"))
    
    def refresh_config():
        self.light.refresh_config()
        self.sensors.refresh_config()
        self.health.refresh_config()

    #Light
    @property
    def light_status(self):
        return self.wrapped_json(self.light.light_status)

    @property
    def light_mode(self):
        return self.wrapped_json(self.light.light_mode)

    @property
    def light_hours(self):
        return self.wrapped_json(self.light.light_hours)

    def set_light_on(self):
        self.light.set_light_on()

    def set_light_off(self):
        self.light.set_light_off()

    def set_light_auto(self):
        self.light.set_light_auto()

    #Sensors
    @property
    def sensors_data(self):
        return self.wrapped_json(self.sensors.sensors_data)

    @property
    def environment_sensors_list(self):
        return self.sensors.get_environment_sensors_list()

    @property
    def plant_sensors_list(self):
        return self.wrapped_json(self.sensors.get_plant_sensors_list())

    @property
    def plants_health(self):
        return self.wrapped_json(self.health.get_health_data())




    
    def get_alarms(self):
        return self.alarm if self.alarm != [] else None

    def reset_alarm(self):
        self.alarm = {"temperature": {"high": [], "low": []},
                      "humidity": {"high": [], "low": []}}    
    
    
    
    def alarms_loop(self):
        temp_limits = gaiaConfig(self.ecosystem).get_climate_parameters("temperature")
        hum_limits = gaiaConfig(self.ecosystem).get_climate_parameters("humidity")
        day = gaiaConfig(self.ecosystem).get_light_parameters()["day"]
        night = gaiaConfig(self.ecosystem).get_light_parameters()["night"]
        data = self.get_sensors_data()
        last_time = datetime.strptime(data[self.ecosystem]["datetime"], "%Y-%m-%d %H:%M:%S").time()

        if day <= last_time <= night:
            for sensor in data[self.ecosystem]["environment"]["temperature"]:
                if data[self.ecosystem]["environment"]["temperature"][sensor] < temp_limits["day"]["min"]:
                    print("temp low")
                elif data[self.ecosystem]["environment"]["temperature"][sensor] > temp_limits["day"]["max"]:
                    print("temp high")
                else:
                    print("temp ok")
            for sensor in data[self.ecosystem]["environment"]["humidity"]:
                if data[self.ecosystem]["environment"]["humidity"][sensor] < hum_limits["day"]["min"]:
                    print("hum low")
                elif data[self.ecosystem]["environment"]["humidity"][sensor] > hum_limits["day"]["max"]:
                    print("hum high")
                else:
                    print("hum ok")

        else:
            for sensor in data[self.ecosystem]["environment"]["temperature"]:
                if data[self.ecosystem]["environment"]["temperature"][sensor] < temp_limits["night"]["min"]:
                    print("temp low")
                elif data[self.ecosystem]["environment"]["temperature"][sensor] > temp_limits["night"]["max"]:
                    print("temp high")
                else:
                    print("temp ok")
            for sensor in data[self.ecosystem]["environment"]["humidity"]:
                if data[self.ecosystem]["environment"]["humidity"][sensor] < hum_limits["night"]["min"]:
                    print("hum low")
                elif data[self.ecosystem]["environment"]["humidity"][sensor] > hum_limits["night"]["max"]:
                    print("hum high")
                else:
                    print("hum ok")