#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""add an option for logging level, which will modify the imported LOGGING_CONFIG dict. Add this option in the config file"""

import logging
import logging.config
import json
import pytz
from threading import Thread, Lock
from datetime import date, datetime, time

from tzlocal import get_localzone
from apscheduler.schedulers.background import BackgroundScheduler

from .config import completeConfig
from .light import gaiaLight
from .sensors import gaiaSensors
from .health import gaiaHealth
from .climate import gaiaClimate


#logging.config.dictConfig(LOGGING_CONFIG)

local_timezone = get_localzone()
lock = Lock()


class datetimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            obj = obj.astimezone(tz=pytz.timezone("UTC"))
            return obj.replace(microsecond=0).isoformat()
        if isinstance(obj, (time)):
            obj = datetime.combine(date.today(), obj)
            obj = obj.astimezone(tz=local_timezone)
            obj = obj.astimezone(tz=pytz.timezone("UTC")).time()
            return obj.replace(microsecond=0).isoformat()


class gaiaEngine(Thread):
    """
    gaiaEngine is the module that controls all the subprocesses related to environment control
    such as gaiaLight, gaiaSensors, gaiaHealth ...
    It is adapted to a thread in the case where multiple instances of gaiaEngine are run on the
    same computer.
    """
    def __init__(self, ecosystem_id):
        super(gaiaEngine, self).__init__()
        self._ecosystem_id = ecosystem_id
        self._config = completeConfig(self._ecosystem_id)
        self._ecosystem_name = self._config.name
        self._logger = logging.getLogger(f"eng.{self._ecosystem_name}")
        self._alarm_logger = logging.getLogger("alarm")

#        self.refresh_alarms()

    def run(self):
        self._logger.info(f"Starting Engine for ecosystem {self._ecosystem_name}")
        self._start_scheduler()
        threads = []
        for func in [self._load_sensors, self._load_light, self._load_health]:
            t = Thread(target = func, args=())
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        self._logger.info(f"Engine for ecosystem {self._ecosystem_name} successfully started")

    def _load_sensors(self):
        self._logger.debug("Loading gaiaSensors")
        self._sensors = gaiaSensors(self._config)
        self._logger.debug("Sensors subroutine successfully loaded")

    def _load_light(self):
        self._logger.debug("Loading gaiaLight")
        self._light = gaiaLight(self._config)
        self._logger.debug("Light subroutine successfully loaded")

    def _load_health(self):
        self._logger.debug("Loading gaiaHealth")
        self._health = gaiaHealth(self._config)
        self._logger.debug("Health subroutine successfully loaded")

    def _load_climate(self):
        self._logger.debug("Loading gaiaClimate")
        self._climate = gaiaClimate(self._config)
        self._logger.debug("Climate subroutine successfully loaded")

    def _start_subroutine(self, subroutine_name):
        pass

    def _stop_subroutine(self, subroutine):
        subroutine_name = subroutine.name
#        try:
        subroutine.stop()
        del subroutine
        self._logger.debug(f"{subroutine_name.capitalize()} subroutine was stopped")
#        except:
#            self._logger.error(f"{subroutine_name.capitalize()} subroutine was not shut down properly")

    def _start_scheduler(self):
        h, m = self._config.HEALTH_LOGGING_TIME.split("h")
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(self.health_routine, trigger="cron",
                               hour=h, minute=m, misfire_grace_time=15*60,
                               id="health")
        self._scheduler.start()

    def _stop_scheduler(self):
        self._logger.info("Closing the tasks scheduler")
        try:
            self._scheduler.remove_job("health")
            self._scheduler.shutdown()
            del self._scheduler
            self._logger.info("The tasks scheduler was closed properly")
        except:
            self._logger.error("The tasks scheduler was not closed properly")

    def stop(self):
        self._logger.info("Stopping engine ...")
        self._stop_scheduler()
        for subroutine in [self._sensors, self._light, self._health]:
            self._stop_subroutine(subroutine)
        self._logger.info("Engine stopped")

    """Plant health part"""
    def health_routine(self):
        mode = self._light.mode
        status = self._light.status
        self.set_light_on()
        self._health.take_picture()
        if mode == "automatic":
            self.set_light_auto()
        else:
            if status:
                self.set_light_on()
            else:
                self.set_light_off()
        self._health.image_analysis()

    """API calls"""
    #Wrap the data into a json and add an ecosystem signature
    def _wrapped_json(self, data):
        wrapped_data = {self._ecosystem_id: data}
        return datetimeEncoder().encode(wrapped_data)

    #Configuration info
    @property
    def name(self):
        return self._ecosystem_name

    @property
    def id(self):
        return self._ecosystem_id

    def update_config(self):
        lock.acquire()
        self._config = completeConfig(self._ecosystem_id)
        lock.release()

    @property
    def config_dict(self):
        return self._wrapped_json(self._config.config_dict)

    """
    def environmental_sensors(self):
        return self._wrapped_json(self._config.get_sensor_list("environment"))

    def plant_sensors(self):
        return self._wrapped_json(self._config.get_sensor_list("plant"))
    """

    #Light
    def update_moments(self):
        self._light.update_moments()

    @property
    def light_info(self):
        status = self._light.status
        mode = self._light.mode
        lighting_hours = self._light.lighting_hours
        info = {"status": status,
                "mode": mode,
                "lighting_hours": lighting_hours
                }
        return self._wrapped_json(info)

    def set_light_on(self):
        self._light.set_light_on()

    def set_light_off(self):
        self._light.set_light_off()

    def set_light_auto(self):
        self._light.set_light_auto()

    #Sensors
    @property
    def sensors_data(self):
        return self._wrapped_json(self._sensors.sensors_data)

    #Health
    @property
    def plants_health(self):
        return self._wrapped_json(self._health.get_health_data())

    #Refresh
    def refresh_config(self):
        self._light.refresh_config()
        self._sensors.refresh_config()
        self._health.refresh_config()

    @property
    def alarms(self):
        return self.alarms
"""
#will need for: for: try:
    def refresh_alarms(self):
        self.alarms_empty = {}
        for measure in self._config.get_measure_list("environment"):
            if (self._config.config_dict["environment"]["day"][measure]["min"] or
                self._config.config_dict["environment"]["day"][measure]["max"] or
                self._config.config_dict["environment"]["night"][measure]["min"] or
                self._config.config_dict["environment"]["night"][measure]["max"]):
                target = self._config.config_dict["environment"]["night"][measure]["target"]
                self.alarms_empty[measure] = {"limit_exceeded" : {"value": target, "sensor": ""}}
                #limit exceeded : {"value": 23; "sensor": "PYDCbGGUcci9EOpO"}

    def alarms_loop(self):
        self.alarms = dict(self.alarms_empty)
        data = self._sensors.sensors_data
        record_time = data["datetime"]
        day = self._config.time_parameters["day"]
        night = self._config.time_parameters["night"]
        if day <= record_time <= night:
            tod = "day"
        else:
            tod = "night"
        for measure in self.alarms:
            for sensor in data["environment"][measure]:
                try:
                    #if measure cross a limit
                    if data["environment"][measure][sensor] <\
                        self._config.config_dict["environment"][tod][measure]["min"]:
                        #check if threshold already crossed before
                        value = data["environment"][measure][sensor]
                        if value < self.alarms[measure]["value"]:
                            self.alarms[measure]["value"] = value
                            self.alarms[measure]["sensor"] = sensor
                except:
                    pass
                try:
                    if data["environment"][measure][sensor] >\
                        self._config.config_dict["environment"][tod][measure]["max"]:
                        #check if threshold already crossed before
                        value = data["environment"][measure][sensor]
                        if value > self.alarms[measure]["value"]:
                            self.alarms[measure]["value"] = value
                            self.alarms[measure]["sensor"] = sensor
                except:
                    pass
"""
