from datetime import datetime, date
import logging
from threading import Event, Lock, Thread
from time import time

from simple_pid import PID

from config import Config
from engine.config_parser import configWatchdog, getConfig, localTZ
from engine.hardware_library import gpioSwitch


Kp = 0.01
Ki = 0.005
Kd = 0.01
lock = Lock()


class gaiaLight:
    NAME = "light"

    def __init__(self, ecosystem: str) -> None:
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
        self._dimmable = {"value": False,  # self._config ...
                          "level": 100}

        self._lights = []

        self._pid = PID(Kp=Kp, Ki=Ki, Kd=Kd, output_limits=(20, 100))
        self._sun_times = {}
        self.update_sun_times()
        self._timer = 0
        self._start_light_loop()

        self._logger.debug("gaiaLight successfully initialized")

    def _tune_light_level(self, hardware_uid: str) -> None:
        # TODO: use PWM
        dim = self._config.IO_dict[hardware_uid]["model"]
        if dim == "dimmable":
            # adjust light level through pwm
            pass

    def _add_light(self, hardware_uid: str) -> None:
        name = self._config.IO_dict[hardware_uid]["name"]
        light = gpioSwitch(
            hardware_uid=hardware_uid,
            address=self._config.IO_dict[hardware_uid]["address"],
            model=self._config.IO_dict[hardware_uid]["model"],
            name=name,
            level=self._config.IO_dict[hardware_uid]["level"],
        )
        light.turn_off()
        self._lights.append(light)
        self._logger.debug(f"Light '{name}' has been set up")

    def _remove_light(self, hardware_uid: str) -> None:
        try:
            index = [h.uid for h in self._lights].index(hardware_uid)
            del self._lights[index]
        except ValueError:
            self._logger.error(f"Light '{hardware_uid}' does not exist")

    def _hardware_setup(self) -> None:
        for light in self._config.get_lights():
            self._add_light(light)

    def _start_light_loop(self) -> None:
        if not self._started:
            self._logger.info("Starting light loop at a frequency of " +
                              f"{1 / Config.LIGHT_LOOP_PERIOD}Hz")
            self._hardware_setup()
            self._stopEvent = Event()
            self._lightLoopThread = Thread(target=self._light_loop, args=())
            self._lightLoopThread.name = f"lightLoop-{self._config.ecosystem_id}"
            self._lightLoopThread.start()
            self._started = True
        else:
            raise RuntimeError

    def _stop_light_loop(self) -> None:
        if self._started:
            self._logger.info("Stopping light loop")
            self._stopEvent.set()
            self._lightLoopThread.join()
            del self._lightLoopThread, self._stopEvent
            self._started = False

    def _light_loop(self) -> None:
        while not self._stopEvent.is_set():
            if self._management:
                if self._timer:
                    if self._timer < time():
                        self._timer = 0
                        self._mode = "automatic"
                self._light_routine()
            self._stopEvent.wait(Config.LIGHT_LOOP_PERIOD)

    """Functions to switch the light on/off either manually or automatically"""
    @staticmethod
    def _is_time_between(begin_time: time, end_time: time,
                         check_time=None) -> bool:
        check_time = check_time or datetime.now().time()
        if begin_time < end_time:
            return begin_time <= check_time < end_time
        else:  # crosses midnight
            return check_time >= begin_time or check_time < end_time

    def _to_dt(self, _time: time) -> datetime:
        # Transforms time to today's datetime. Needed to use timedelta
        _date = date.today()
        naive_dt = datetime.combine(_date, _time)
        aware_dt = naive_dt.astimezone(self._timezone)
        return aware_dt

    def _lighting(self) -> bool:
        lighting = False
        if self._mode == "automatic":
            if self._method == "fixed":
                lighting = self._is_time_between(self._sun_times["day"], self._sun_times["night"])

            elif self._method == "place":
                lighting = self._is_time_between(self._sun_times["sunrise"], self._sun_times["sunset"])

            elif self._method == "elongate":
                now = datetime.now().astimezone(self._timezone).time()
                """
                need to change this to calculate it once per day, or at method change
                
                """

                morning_end = (self._to_dt(self._sun_times["sunrise"]) + self._sun_times["offset"]).time()
                evening_start = (self._to_dt(self._sun_times["sunset"]) - self._sun_times["offset"]).time()
                # If time between lightning hours
                if ((self._sun_times["day"] <= now < morning_end) or
                        (evening_start <= now < self._sun_times["night"])):
                    lighting = True
                else:
                    lighting = False

        elif self._mode == "manual":
            if self._status["current"]:
                lighting = True
            else:
                lighting = False
        return lighting

    def _light_routine(self) -> None:
        # If lighting == True, lights should be on
        if self._lighting():
            # If lights were closed, turn them on
            if not self._status["last"]:
                # Reset pid so there is no internal value overshoot
                self._pid.reset()
                self._status["current"] = True
                for light in self._lights:
                    light.turn_on()
                if self._mode == "automatic":
                    self._logger.info("Lights have been automatically turned on")
        # If lighting == False, lights should be off
        else:
            # If lights were opened, turn them off
            if self._status["last"]:
                self._status["current"] = False
                for light in self._lights:
                    light.turn_off()
                if self._mode == "automatic":
                    self._logger.info("Lights have been automatically turned off")
        self._status["last"] = self._status["current"]

    """API calls"""
    def update_sun_times(self) -> None:
        # lock thread as all the whole dict should be transformed at the "same time"
        # add a if method == elongate or mimic, and if connected
        lock.acquire()
        try:
            self._sun_times.update(self._config.time_parameters)
            self._sun_times.update(self._config.sun_times)
            sunrise = self._to_dt(self._sun_times["sunrise"])
            twilight_begin = self._to_dt(self._sun_times["twilight_begin"])
            self._sun_times["offset"] = sunrise - twilight_begin
        finally:
            lock.release()

    @property
    def status(self) -> bool:
        return self._status["current"]

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def method(self) -> str:
        return self._method

    @property
    def lighting_hours(self) -> dict:
        hours = {
            "morning_start": self._sun_times["day"],
            "morning_end": (self._to_dt(self._sun_times["sunrise"]) + self._sun_times["offset"]).time(),
            "evening_start": (self._to_dt(self._sun_times["sunset"]) - self._sun_times["offset"]).time(),
            "evening_end": self._sun_times["night"]
        }
        return hours

    @property
    def light_info(self) -> dict:
        return {"status": self.status,
                "mode": self.mode,
                "method": self.method,
                "lighting_hours": self.lighting_hours
                }

    """
    @method.setter(self, method):
        assert method in ["elongate", "fixed", "mimic"]
        if method == "elongate":
            self.update_sun_times
        self._method = method
    """

    """
    add countdown
    """

    def set_light_on(self, countdown: float = 0) -> None:
        self._mode = "manual"
        self._status["current"] = True
        additional_message = ""
        if countdown:
            additional_message = f" for {countdown} seconds"
        self._logger.info(f"Lights have been manually turned on{additional_message}")

    def set_light_off(self, countdown: float = 0) -> None:
        self._mode = "manual"
        self._status["current"] = False
        additional_message = ""
        if countdown:
            additional_message = f" for {countdown} seconds"
        self._logger.info(f"Lights have been manually turned off{additional_message}")

    def set_light_auto(self) -> None:
        self._mode = "automatic"
        self._logger.info("Lights have been turned to automatic mode")

    def start_countdown(self, countdown: float) -> None:
        if not isinstance(countdown, float):
            raise ValueError("Countdown must be an int")
        if self._status["current"]:
            self.set_light_off(countdown)
        else:
            self.set_light_on(countdown)
        self._timer = time() + countdown

    def get_countdown(self) -> float:
        return round(self._timer - time(), 2)

    def increase_countdown(self, additional_time: float) -> None:
        if self._timer:
            self._logger.info("Increasing timer by {additional_time} seconds")
            self._timer += additional_time
        else:
            self.start_countdown(additional_time)

    def decrease_countdown(self, decrease_time: float) -> None:
        if self._timer:
            self._logger.info("Decreasing timer by {decrease_time} seconds")
            self._timer -= decrease_time
        else:
            raise AttributeError("No timer set, you cannot reduce the countdown")

    @property
    def PID_tunings(self) -> tuple:
        """Returns the tunings used by the controller as a tuple: (Kp, Ki, Kd)"""
        return self._pid.tunings

    @PID_tunings.setter
    def PID_tunings(self, tunings: tuple) -> None:
        """:param tunings: tuple (Kp, Ki, Kd)"""
        self._pid.tunings(tunings)

    """Functions to update config objects"""

    def refresh_hardware(self) -> None:
        self._hardware_setup()

    def stop(self) -> None:
        #        self._stop_scheduler()
        self._stop_light_loop()
