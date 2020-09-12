#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"Add if lamps are/could be dimable in config. If dimable and pwm activated: then do the PID loop "
"add a username id when light is manually changed"
import logging
from time import sleep
from datetime import datetime, date
from threading import Lock, Thread

from simple_pid import PID

try:
    import RPi.GPIO as GPIO
except:
    from stupid_PI import GPIO

Kp = 0.01
Ki = 0.005
Kd = 0.01
lock = Lock()

LIGHT_FREQUENCY = 0.5

class gaiaLight:
    def __init__(self, completeConfigObject):
        self._config = completeConfigObject
        self._ecosystem = self._config.name
        self.name = "light"
        self._logger = logging.getLogger(f"eng.{self._ecosystem}.Light")
        self._logger.debug(f"Initializing gaiaLight for {self._ecosystem}")

        self._timezone = self._config.local_timezone
        self._pid = PID(Kp=Kp, Ki=Ki, Kd=Kd, output_limits=(20, 100))

        self._management = self._config.get_management("lighting")
        self._status = False
        self._mode = "automatic"
        if self._config.is_connected():
            self._method = "elongate"
        else:
            self._method = "fixed"
        self._adaptative = False
        self._moments = {}
        self.update_moments()

        self._start_light_loop()

        self._logger.debug(f"gaiaLight successfully initialized for {self._ecosystem}")

    def __call__(self, light_intensity):
        if self._status:
            #allow to receive light intensity value from sensors to update light powerintensity
            self.light_intensity = light_intensity
            #call pid here
            if self._adaptative:
                for light_id in self._config.get_hardware_group(""):
                    pass

    """will require output from sensors, via engine. maybe use a call??"""
    def _tune_light_level(self, light_id):
        dim = self._config.hardware_dict[light_id]["model"]
        if dim == "dimmable":
            #adjust light level through pwm
            pass

    def _hardware_setup(self):
        GPIO.setmode(GPIO.BOARD)
        for light in self._config.get_hardware_group("light", "environment"):
            pin = self._config.hardware_dict[light]["pin"]
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)
            light_name = self._config.hardware_dict[light]["name"]
            self._logger.debug(f"Light '{light_name}' has been set up")

    def _start_light_loop(self):
        self._logger.info(f"Starting light loop for {self._ecosystem} at a frequency of {1/LIGHT_FREQUENCY}Hz")
        self._hardware_setup()
        self._run = True
        self._thread = Thread(target=self._light_loop, args=())
        self._thread.start()

    def _stop_light_loop(self):
        self._logger.info(f"Stopping light loop for {self._ecosystem}")
        try:
            self._run = False
            self._thread.join()
            del self._thread
            self._logger.debug(f"Light loop was stopped for {self._ecosystem}")
        except:
            self._logger.error(f"Light loop was not stopped properly for {self._ecosystem}")


    def _light_loop(self):
        while self._run:
            if self._management:
                self._light_routine()
            sleep(LIGHT_FREQUENCY)


    """Functions to switch the light on/off either manually or automatically"""
    def _is_time_between(self, begin_time, end_time, check_time=None):
        check_time = check_time or datetime.now().time()
        if begin_time < end_time:
            return check_time >= begin_time and check_time < end_time
        else: # crosses midnight
            return check_time >= begin_time or check_time < end_time

    def _to_dt(self, mytime):
        #Transforms time to today's datetime. Needed to use timedelta
        day = date.today()
        naive = datetime.combine(day, mytime)
        aware = naive.astimezone(self._timezone)
        return aware

    @property
    def _lighting(self):
        if self._mode == "automatic":
            if self._method == "fixed":
                lighting = self._is_time_between(self._moments["day"], self._moments["night"])

            elif self._method == "place":
                lighting = self._is_time_between(self._moments["sunrise"], self._moments["sunset"])

            elif self._method == "elongate":
                now = datetime.now().astimezone(self._timezone).time()
                morning_end = (self._to_dt(self._moments["sunrise"]) + self._moments["offset"]).time()
                evening_start = (self._to_dt(self._moments["sunset"]) - self._moments["offset"]).time()
                #If time between lightning hours
                if ((self._moments["day"] <= now < morning_end) or
                    (evening_start <= now < self._moments["night"])):
                    lighting = True
                else:
                    lighting = False

        elif self._mode == "manual":
            if self._status:
                lighting = True
            else:
                lighting = False
        return lighting

    def _light_routine(self):
        #If lighting = True, lights should be on
        if self._lighting:
            #If lights were closed, turn them on
            if not self._status:
                #Reset pid so there is no internal value overshoot
                self._pid.reset()
                self._status = True
                for light in self._config.get_hardware_group("light", "environment"):
                    pin = self._config.hardware_dict[light]["pin"]
                    GPIO.output(pin, GPIO.HIGH)
                self._logger.info(f"Lights have been automatically turned on for {self._ecosystem}")
        #If lighting = False, lights should be off
        else:
            #If lights were opened, turn them off
            if self._status:
                self._status = False
                for light in self._config.get_hardware_group("light", "environment"):
                    pin = self._config.hardware_dict[light]["pin"]
                    GPIO.output(pin, GPIO.LOW)
                self._logger.info(f"Lights have been automatically turned on for {self._ecosystem}")

    """API calls"""
    def update_moments(self):
        #lock thread as all the whole dict should be transformed at the "same time"
        #add a if method == elongate or mimic, and if connected
        lock.acquire()
        try:
            self._moments.update(self._config.time_parameters)
            self._moments.update(self._config.moments)
            sunrise = self._to_dt(self._moments["sunrise"])
            twilight_begin = self._to_dt(self._moments["twilight_begin"])
            self._moments["offset"] = sunrise - twilight_begin
        finally:
            lock.release()

    def set_light_on(self):
        GPIO.output(self.pin_light, GPIO.HIGH)
        self._status = True
        self._mode = "manual"
        self._logger.info(f"Lights have been manually turned on in {self._ecosystem}")

    def set_light_off(self):
        GPIO.output(self.pin_light, GPIO.LOW)
        self._status = False
        self._mode = "manual"
        self._logger.info(f"Lights have been manually turned off in {self._ecosystem}")

    def set_light_auto(self):
        self._mode = "automatic"
        self._logger.info(f"Lights have been turned to automatic mode in {self._ecosystem}")
#        self._light_routine()

    @property
    def status(self):
        return self._status

    @property
    def mode(self):
        return self._mode

    @property
    def method(self):
        return self._method
    """
    @method.setter(self, method):
        assert method in ["elongate", "fixed", "mimic"]
        if method == "elongate":
            self.update_moments
        self._method = method
    """

    @property
    def lighting_hours(self):
        hours = {"morning_start": self._moments["day"].time(),
                 "morning_end": (self._moments["sunrise"] + self._moments["offset"]).time(),
                 "evening_start": (self._moments["sunset"] - self._moments["offset"]).time(),
                 "evening_end": self._moments["night"].time()
                 }
        return hours

    @property
    def PID_tunings(self):
        """Returns the tunings used by the controller as a tuple: (Kp, Ki, Kd)"""
        return self._pid.tunings

    @PID_tunings.setter
    def PID_tunings(self, tunings):
        """:param tunings: tuple (Kp, Ki, Kd)"""
        self._pid.tunings(tunings)

    """Functions to update config objects"""
    def refresh_hardware(self):
        self._hardware_setup()

    def stop(self):
#        self._stop_scheduler()
        self._stop_light_loop()
