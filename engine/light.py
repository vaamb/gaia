#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"add a for lamp in lamps loop. Add if lamps are/could be dimable in config. If dimable and pwm activated: then do the PID loop "
"add a username id when light is manually changed"
import logging
import threading
from time import sleep
from datetime import datetime, date
import pytz
import requests
import json
from apscheduler.schedulers.background import BackgroundScheduler
from simple_pid import PID

#import RPi.GPIO as GPIO
from stupid_GPIO import stupid_GPIO
GPIO = stupid_GPIO()

from .config import gaiaConfig

ECOSYSTEM = "B612"
Kp = 0.01
Ki = 0.005
Kd = 0.01

class gaiaLight:
    def __init__(self, ecosystem):
        self.ecosystem = ecosystem
        self.config = gaiaConfig(self.ecosystem)
        self.timezone = self.config.local_timezone

        self.logger = logging.getLogger("eng.{}.Light".format(self.ecosystem))
        self.logger.info("Initializing gaiaLight for {}".format(self.ecosystem))

        self.pid = PID(Kp=Kp, Ki=Ki, Kd=Kd, output_limits=(20, 100))

        self._init_light_dict()
        self._get_sun_times()
        self._light_config()
        self._hardware_setup()

        self._start_light_loop()
        self._scheduler()

        self.logger.info("gaiaLight successfully initialized for {}".format(self.ecosystem))

    def __call__(self, light_intensity):
        self.light_intensity = light_intensity

    """Private functions"""
    def _init_light_dict(self):
        self.light = {}
        self.light["status"] = False
        self.light["mode"] = "automatic"
        self.light["method"] = "elongate" #choose between "fixed", "place", "elongate"
        self.light["adaptative"] = False

    def _to_datetime(self, mytime):
        #Transforms time to today's datetime
        naive = datetime.combine(date.today(), mytime)
        aware = naive.astimezone(pytz.timezone(self.timezone))
        return aware

    def _light_config(self):
        self.light.update(self.config.light_parameters)
        self.light.update(self.config.sun_times)
        self.light["day"] = self._to_datetime(self.light["day"])
        self.light["twilight_begin"] = self._to_datetime(self.light["twilight_begin"])
        self.light["sunrise"] = self._to_datetime(self.light["sunrise"])
        self.light["sunset"] = self._to_datetime(self.light["sunset"])
        self.light["night"] = self._to_datetime(self.light["night"])
        self.light["offset"] = self.light["sunrise"] - self.light["twilight_begin"]
        self.light["method"] = "fixed"
        self.light["adaptative"] = False

    def _hardware_setup(self):
        GPIO.setmode(GPIO.BOARD)
        for light in self.config.get_light_list():
            pin = self.config.get_hardware_pin(light)
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)
            light_name = self.config.get_hardware_name(light)
            self.logger.info("Light '{}' has set up".format(light_name)) #maybe use in logger?

    def _light_loop(self):
        light_frequency = 0.5
        self.logger.info("Starting gaiaLight loop for {} at a frequency of {}Hz"
                         .format(self.ecosystem, 1/light_frequency))
        while True:
            self.light_routine()
            sleep(light_frequency)
            self.logger.debug("Light status is {}".format(self.light["status"]))

    def _start_light_loop(self):
        thread = threading.Thread(target = self._light_loop, args=())
        thread.start()

    def _get_sun_times(self):
        trials = 5
        home_city = self.config.home_city
        for count in range(trials):
            try:

                if count == 0:
                    self.logger.info("Trying to update sunrise and sunset times for {} on sunrise-sunset.org"
                                     .format(home_city))
                else:
                    self.logger.info("Retrying to update sunrise and sunset times -- trial #{}"
                                     .format(count+1))
                coordinates = self.config.home_coordinates
                latitude = coordinates["latitude"]
                longitude = coordinates["longitude"]
                data = requests.get("https://api.sunrise-sunset.org/json?lat="
                                    + str(latitude) + "&lng=" + str(longitude)).json()

                results = data["results"]
                outfile = open("engine/cache/sunrise.cch", "w")
                json.dump(results, outfile)
                outfile.close()
                self.logger.info("Sunrise and sunset times have successfully been updated")
                self.refresh_light_config()
                self.logger.info("Sunrise and sunset times successfully updated")
                break
            except:
                if count != trials-1:
                    self.logger.info("Failed to update sunrise and sunset times, retrying")
                    sleep(0.2)
                elif count == trials-1:
                    self.logger.error("Failed to update sunrise and sunset times for {}"
                                      .format(home_city))

    def _scheduler(self):
        self.sched = BackgroundScheduler(daemon = True)
        self.sched.add_job(self._get_sun_times, "cron", hour = "1", misfire_grace_time = 15*60)
        #for now, automatic, then only when asked. Change the log too
        self.sched.add_job(self.refresh_light_config, "cron", second = "*/10", misfire_grace_time = 15)
        self.logger.info("Starting gaiaLight background scheduler for {}".format(self.ecosystem))
        self.sched.start()
        self.logger.info("gaiaLight background scheduler started for {}".format(self.ecosystem))

    """Functions to switch the light on/off either manually or automatically"""
    def light_routine(self):
        now = datetime.now().astimezone(pytz.timezone(self.timezone))

        if self.light["mode"] == "automatic":
            try:
                if self.light["method"] == "fixed":
                    #If time between lightning hours
                    if (self.light["day"] <= now < self.light["night"]):
                        switch = True
                    else:
                        switch = False

                elif self.light["method"] == "elongate":
                    #If time between lightning hours
                    if (self.light["day"] <= now < (self.light["sunrise"] + self.light["offset"]) or
                        (self.light["sunset"] - self.light["offset"]) <= now < self.light["night"]):
                        switch = True
                    else:
                        switch = False

                elif self.light["method"] == "place":
                    pass

            #When config refreshes during the datetime comparison, an error might occur as
            #part of config is in time, reste in datetime.
            #In this case do nothing, the problem will be solved next turn
            except: 
                pass

            try:
                #If switch = True, lights should be on
                if switch:               
                    #All lights on
                    for light in self.config.get_light_list():
                        pin = self.config.get_hardware_pin(light)
                        GPIO.output(pin, GPIO.HIGH)

                    #If light status was previously False, change it to True
                    if not self.light["status"]:
                        self.light["status"] = True
                        self.logger.info("Lights have been automatically turned on for {}"
                                         .format(self.ecosystem))
                        #Reset pid so there is no overshoot
                        self.pid.reset()    
                #If switch = False, lights should be off
                else:
                    #All lights off
                    for light in self.config.get_light_list():
                        pin = self.config.get_hardware_pin(light)
                        GPIO.output(pin, GPIO.LOW)       
                    #If light status was previously True, change it to False
                    if self.light["status"]:
                        self.light["status"] = False
                        self.logger.info("Lights have been automatically turned on for {}"
                                         .format(self.ecosystem))
            #Same rem as before
            except:
                pass
        
        #Once the automatic mode has run, adjust light level
        if self.light["status"]:
            for light in self.config.get_light_list():
                pin = self.config.get_hardware_pin(light)
                self.tune_light_level(light)

    """will require output from sensors, via engine. maybe use a call??"""
    def tune_light_level(self, light_id):
        if self.light["adaptative"]:
            dim = self.config.get_hardware_model(light_id)
            if dim == "dimmable":
                #adjust light level through pwm
                pass

    """Functions to interact with higher modules"""
    def set_light_on(self):
        GPIO.output(self.pin_light, GPIO.HIGH)
        self.light["status"] = True
        self.light["mode"] = "manual"
        self.logger.warning("Lights have been manually turned on in {}"
                         .format(self.ecosystem))

    def set_light_off(self):
        GPIO.output(self.pin_light, GPIO.LOW)
        self.light["status"] = False
        self.light["mode"] = "manual"
        self.logger.warning("Lights have been manually turned off in {}"
                         .format(self.ecosystem))

    def set_light_auto(self):
        self.light["mode"] = "automatic"
        self.logger.warning("Lights have been turned to automatic mode in {}"
                         .format(self.ecosystem))
        self.light_routine()

    @property
    def light_status(self):
        return self.light["status"]

    @property
    def light_mode(self):
        return self.light["mode"]

    @property
    def lighting_hours(self):
        hours = {"morning_start": self.light["day"].time(),
                 "morning_end": (self.light["sunrise"] + self.light["offset"]).time(),
                 "evening_start": (self.light["sunset"] - self.light["offset"]).time(),
                 "evening_end": self.light["night"].time()}
        return hours
    
    @property
    def PID_tunings(self):
        """Returns the tunings used by the controller as a tuple: (Kp, Ki, Kd)"""
        self.pid.tunings
    
    @PID_tunings.setter
    def PID_tunings(self, tunings):
        """:param tunings: tuple (Kp, Ki, Kd)"""
        self.pid.tunings(tunings)

    """Functions to update config objects"""
    def refresh_config(self):
        self.config = gaiaConfig(self.ecosystem)

    def refresh_light_config(self):
        self.refresh_config()
        self._light_config()
#        self.logger.info("Light configuration has been updated for {}"
#                         .format(self.ecosystem))

    def refresh_hardware(self):
        self._hardware_setup()

if __name__ == "__main__":
    light = gaiaLight("B612")