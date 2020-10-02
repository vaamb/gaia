# -*- coding: utf-8 -*-
"Add if lamps are/could be dimable in config. If dimable and pwm activated: then do the PID loop "
import logging
from time import time
from datetime import datetime, date
from threading import Lock, Thread, Event

from simple_pid import PID
try:
    import RPi.GPIO as GPIO
except:
    from stupid_PI import GPIO
    
from config import Config
from engine.config_parser import configWatchdog, getConfig, localTZ


Kp = 0.01
Ki = 0.005
Kd = 0.01
lock = Lock()


class gaiaLight:
    NAME = "light"
    def __init__(self, ecosystem):
        configWatchdog.start()
        self._config = getConfig(ecosystem)
        self._ecosystem = self._config.name
        self._logger = logging.getLogger(f"eng.{self._ecosystem}.Light")
        self._logger.debug("Initializing gaiaLight")
        self._timezone = localTZ

        self._started = False
        self._management = self._config.get_management("lighting")
        self._status = {"current": False, "last": False}
        self._mode = "automatic"
        self._method = self._config.light_method
        self._dimable = {"value": False, #self._config.light_dimmable
                         "level": 100}
        self._pid = PID(Kp=Kp, Ki=Ki, Kd=Kd, output_limits=(20, 100))
        self._moments = {}
        self.update_moments()
        self._timer = 0
        self._start_light_loop()

        self._logger.debug("gaiaLight successfully initialized")

    def __call__(self, light_intensity):
        if self._status["current"]:
            #allow to receive light intensity value from sensors to update light powerintensity
            self.light_intensity = light_intensity
            #call pid here
            if self._adaptative:
                for light_id in self._config.get_IO_group(""):
                    pass

    """will require output from sensors, via engine. maybe use a call??"""
    def _tune_light_level(self, light_id):
        dim = self._config.IO_dict[light_id]["model"]
        if dim == "dimmable":
            #adjust light level through pwm
            pass

    def _hardware_setup(self):
        GPIO.setmode(GPIO.BOARD)
        
        for light in self._config.get_lights():
            pin = self._config.IO_dict[light]["pin"]
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)
            light_name = self._config.IO_dict[light]["name"]
            self._logger.debug(f"Light '{light_name}' has been set up")

    def _start_light_loop(self):
        if not self._started:
            self._logger.info("Starting light loop at a frequency of " +
                              f"{1/Config.LIGHT_LOOP_PERIOD}Hz")
            self._hardware_setup()
            self._stopEvent = Event()
            self._lightLoopThread = Thread(target=self._light_loop, args=())
            self._lightLoopThread.name = f"lightLoop-{self._config.ecosystem_id}"
            self._lightLoopThread.start()
            self._started = True
        else:
            raise RuntimeError

    def _stop_light_loop(self):
        if self._started:
            self._logger.info("Stopping light loop")
            self._stopEvent.set()
            self._lightLoopThread.join()
            del self._lightLoopThread, self._stopEvent
            self._started = False

    def _light_loop(self):
        while not self._stopEvent.is_set():
            if self._management:
                if self._timer:
                    if self._timer < time():
                        self._timer = 0
                        self._mode = "automatic"
                self._light_routine()
            self._stopEvent.wait(Config.LIGHT_LOOP_PERIOD)

    """Functions to switch the light on/off either manually or automatically"""
    def _is_time_between(self, begin_time, end_time, check_time=None):
        check_time = check_time or datetime.now().time()
        if begin_time < end_time:
            return check_time >= begin_time and check_time < end_time
        else: # crosses midnight
            return check_time >= begin_time or check_time < end_time

    def _to_dt(self, _time):
        #Transforms time to today's datetime. Needed to use timedelta
        _date = date.today()
        naive_dt = datetime.combine(_date, _time)
        aware_dt = naive_dt.astimezone(self._timezone)
        return aware_dt

    def _lighting(self):
        if self._mode == "automatic":
            if self._method == "fixed":
                lighting = self._is_time_between(self._moments["day"], self._moments["night"])

            elif self._method == "place":
                lighting = self._is_time_between(self._moments["sunrise"], self._moments["sunset"])

            elif self._method == "elongate":
                now = datetime.now().astimezone(self._timezone).time()
                """
                need to change this to calculate it once per day, or at method change
                
                """
                
                
                morning_end = (self._to_dt(self._moments["sunrise"]) + self._moments["offset"]).time()
                evening_start = (self._to_dt(self._moments["sunset"]) - self._moments["offset"]).time()
                #If time between lightning hours
                if ((self._moments["day"] <= now < morning_end) or
                    (evening_start <= now < self._moments["night"])):
                    lighting = True
                else:
                    lighting = False

        elif self._mode == "manual":
            if self._status["current"]:
                lighting = True
            else:
                lighting = False
        return lighting

    def _light_routine(self):
        #If lighting == True, lights should be on
        if self._lighting():
            #If lights were closed, turn them on
            if not self._status["last"]:
                #Reset pid so there is no internal value overshoot
                self._pid.reset()
                self._status["current"] = True
                for light in self._config.get_IO_group("light", "environment"):
                    pin = self._config.IO_dict[light]["pin"]
                    GPIO.output(pin, GPIO.HIGH)
                if self._mode == "automatic":
                    self._logger.info("Lights have been automatically turned on")
        #If lighting == False, lights should be off
        else:
            #If lights were opened, turn them off
            if self._status["last"]:
                self._status["current"] = False
                for light in self._config.get_IO_group("light", "environment"):
                    pin = self._config.IO_dict[light]["pin"]
                    GPIO.output(pin, GPIO.LOW)
                if self._mode == "automatic":
                    self._logger.info("Lights have been automatically turned off")
        self._status["last"] = self._status["current"]

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

    @property
    def status(self):
        return self._status["current"]

    @property
    def mode(self):
        return self._mode

    @property
    def method(self):
        return self._method

    @property
    def lighting_hours(self):
        hours = {
            "morning_start": self._moments["day"],
            "morning_end": (self._to_dt(self._moments["sunrise"]) + self._moments["offset"]).time(),
            "evening_start": (self._to_dt(self._moments["sunset"]) - self._moments["offset"]).time(),
            "evening_end": self._moments["night"]
                 }
        return hours
    
    @property
    def light_info(self):
        return {"status": self.status,
                "mode": self.mode,
                "method": self.method,
                "lighting_hours": self.lighting_hours
                }

    """
    @method.setter(self, method):
        assert method in ["elongate", "fixed", "mimic"]
        if method == "elongate":
            self.update_moments
        self._method = method
    """
    
    
    """
    add countdown
    
    
    """

    def set_light_on(self, countdown=None):
        self._mode = "manual"
        self._status["current"] = True
        additionnal_message = ""
        if countdown:
            additionnal_message = f" for {countdown} seconds"
        self._logger.info(f"Lights have been manually turned on{additionnal_message}")

    def set_light_off(self, countdown=None):
        self._mode = "manual"
        self._status["current"] = False
        additionnal_message = ""
        if countdown:
            additionnal_message = f" for {countdown} seconds"
        self._logger.info(f"Lights have been manually turned off{additionnal_message}")

    def set_light_auto(self):
        self._mode = "automatic"
        self._logger.info("Lights have been turned to automatic mode")

    def start_countdown(self, countdown):
        if not isinstance(countdown, int):
            raise ValueError("Countdown must be an int")
        if self._status["current"]:
            self.set_light_off(countdown)
        else:
            self.set_light_on(countdown)
        self._timer = time() + countdown

    def get_countdown(self):
        return round(self._timer - time(), 2)

    def increase_countdown(self, additionnal_time):
        if self._timer:
            self._logger.info("Increasing timer by {additionnal_time} seconds")
            self._timer += additionnal_time
        else:
            self.start_countdown(additionnal_time)

    def decrease_countdown(self, decrease_time):
        if self._timer:
            self._logger.info("Decreasing timer by {decrease_time} seconds")
            self._timer -= decrease_time
        else:
            raise AttributeError("No timmer set, you cannot reduce the countdown")

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