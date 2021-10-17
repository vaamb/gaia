from datetime import datetime, date
from threading import Event, Lock, Thread
import time

from config import Config
from src.utils import localTZ
from src.hardware.actuators import gpioSwitch
from src.subroutines.template import SubroutineTemplate


Kp = 0.01
Ki = 0.005
Kd = 0.01
lock = Lock()


class gaiaLight(SubroutineTemplate):
    NAME = "light"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._timezone = localTZ
        self._status = {"current": False, "last": False}
        self._mode = "automatic"
        # TODO: fall back values if not using light
        self._method = self._config.light_method
        self._dimmable = {"value": False,  # self._config ...
                          "level": 100}
        self._lights = []
        self._pid = None
        # TODO: get sun_times day/night from config. If not precised: display None, and use 8/20h
        self._sun_times = {"day": None,
                           "night": None}
        self._stopEvent = Event()
        self._timer: time.monotonic() = 0.0
        self._finish__init__()

    def _tune_light_level(self, hardware_uid: str) -> None:
        # TODO: use PWM
        dim = self._config.IO_dict[hardware_uid]["model"]
        if dim == "dimmable":
            # adjust light level through pwm
            pass

    def _add_light(self, hardware_uid: str) -> None:
        name = self._config.IO_dict[hardware_uid]["name"]
        light = gpioSwitch(
            subroutine=self,
            uid=hardware_uid,
            name=name,
            address=self._config.IO_dict[hardware_uid]["address"],
            model=self._config.IO_dict[hardware_uid]["model"],
            type="light",
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

            self._lightLoopThread = Thread(target=self._light_loop, args=())
            self._lightLoopThread.name = f"lightLoop-{self._config.ecosystem_id}"
            self._lightLoopThread.start()
        else:
            raise RuntimeError

    def _stop_light_loop(self) -> None:
        if self._started:
            self._logger.info("Stopping light loop")
            self._stopEvent.set()
            self._lightLoopThread.join()
            del self._lightLoopThread, self._stopEvent

    def _light_loop(self) -> None:
        while not self._stopEvent.is_set():
            if self._timer:
                if self._timer < time.monotonic():
                    self._timer = 0
                    self._mode = "automatic"
            self._light_routine()
            self._stopEvent.wait(Config.LIGHT_LOOP_PERIOD)

    """Functions to switch the light on/off either manually or automatically"""
    @staticmethod
    def _is_time_between(begin_time: time, end_time: time,
                         check_time=None) -> bool:
        check_time = check_time or datetime.now().time()
        try:
            if begin_time < end_time:
                return begin_time <= check_time < end_time
            else:  # crosses midnight
                return check_time >= begin_time or check_time < end_time
        except TypeError:
            # one of times is a none
            return False

    def _to_dt(self, _time: time) -> datetime:
        # Transforms time to today's datetime. Needed to use timedelta
        _date = date.today()
        naive_dt = datetime.combine(_date, _time)
        aware_dt = naive_dt.astimezone(self._timezone)
        return aware_dt

    def _lighting(self) -> bool:
        lighting = False
        if self._mode == "automatic":
            lighting = self.expected_status
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
#                self._pid.reset()
                self._status["current"] = True
                for light in self._lights:
                    light.turn_on()
                if self._mode == "automatic":
                    self._logger.info("Lights have been automatically turned on")
                    if self._engine.socketIO_client:
                        try:
                            self._engine.socketIO_client\
                                .namespace_handlers["/gaia"]\
                                .on_send_light_data(
                                    ecosystem_uids=self._config.uid)
                        except AttributeError as e:
                            self._logger.error(e)
        # If lighting == False, lights should be off
        else:
            # If lights were opened, turn them off
            if self._status["last"]:
                self._status["current"] = False
                for light in self._lights:
                    light.turn_off()
                if self._mode == "automatic":
                    self._logger.info("Lights have been automatically turned off")
                    if self._engine.socketIO_client:
                        try:
                            self._engine.socketIO_client.namespace_handlers[
                                "/gaia"].on_send_light_data(
                                ecosystem_uids=(self._uid,))
                        except AttributeError as e:
                            self._logger.error(e)
        self._status["last"] = self._status["current"]

    def _start(self):
        # TODO: check that the ecosystem has day and night parameters
        now = datetime.now()
        if now.date() != self.engine.manager.last_sun_times_update.date():
            self.engine.manager.refresh_sun_times()
        self.update_sun_times(send=True)
        self._start_light_loop()

    def _stop(self):
        self._stop_light_loop()
        self._sun_times = {}

    """API calls"""
    def update_sun_times(self, send=True) -> None:
        # TODO: check if it works when not using elongate
        # lock thread as all the whole dict should be transformed at the "same time"
        with lock:
            self._sun_times.update(self._config.time_parameters)
            if self._sun_times["day"]:
                try:
                    # TODO: move this outside,
                    self._sun_times.update(self._config.sun_times)
                    sunrise = self._to_dt(self._sun_times["sunrise"])
                    twilight_begin = self._to_dt(self._sun_times["twilight_begin"])
                    self._sun_times["offset"] = sunrise - twilight_begin
                    self._sun_times["morning_end"] = \
                        (self._to_dt(self._sun_times["sunrise"]) + self._sun_times["offset"]).time()
                    self._sun_times["evening_start"] = \
                        (self._to_dt(self._sun_times["sunset"]) - self._sun_times["offset"]).time()
                except KeyError:
                    # No sun times available in config/cache
                    pass

        if self._engine.socketIO_client and send:
            try:
                self._engine.socketIO_client.namespace_handlers[
                    "/gaia"].on_send_light_data(ecosystem_uids=(self._uid, ))
            except AttributeError as e:
                self._logger.error(e)

    @ property
    def expected_status(self) -> bool:
        lighting = False
        if self._method == "fixed":
            lighting = self._is_time_between(self._sun_times["day"],
                                             self._sun_times["night"])
        #TODO: try, else use fixed
        elif self._method == "place":
            lighting = self._is_time_between(self._sun_times["sunrise"],
                                             self._sun_times["sunset"])

        elif self._method == "elongate":
            now = datetime.now().astimezone(self._timezone).time()
            # If time between lightning hours
            if ((self._sun_times["day"] <= now < self._sun_times["morning_end"]) or
                    (self._sun_times["evening_start"] <= now < self._sun_times[
                        "night"])):
                lighting = True
            else:
                lighting = False
        return lighting

    @property
    def light_status(self) -> bool:
        return self._status["current"]

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        assert value in ("automatic", "manual")
        self._mode = value

    @property
    def method(self) -> str:
        return self._method

    @method.setter
    def method(self, value: str) -> None:
        assert value in ("elongate", "fixed", "mimic")
        if value in ("elongate", "mimic"):
            self.update_sun_times(send=True)
        self._method = value

    @property
    def lighting_hours(self) -> dict:
        hours = {
            "morning_start": self._sun_times["day"],
            "evening_end": self._sun_times["night"]
        }
        try:
            hours["morning_end"] = ((self._to_dt(self._sun_times["sunrise"]) +
                                     self._sun_times["offset"]).time())
            hours["evening_start"] = ((self._to_dt(self._sun_times["sunset"]) -
                                       self._sun_times["offset"]).time())
        except KeyError:
            hours["morning_end"] = None
            hours["evening_start"] = None
        return hours

    @property
    def timer(self) -> float:
        if self._timer:
            if self._timer > time.monotonic():
                return time.monotonic() - self._timer
        return 0.0

    @property
    def light_info(self) -> dict:
        return {**{
            "status": (self.light_status if self.mode == "manual"
                             else self.expected_status),
            "mode": self.mode,
            "method": self.method,
            "timer": self.timer,
        }, **self.lighting_hours}

    def turn_light(self, mode="automatic", countdown: float = 0.0):
        if self._started:
            if mode == "automatic":
                self._mode = "automatic"
                self._logger.info("Lights have been turned to automatic mode")
            elif mode in ("on", "off"):
                self._mode = "manual"
                new_status = False
                if mode == "on":
                    new_status = True
                self._status["current"] = new_status
                additional_message = ""
                if countdown:
                    self._timer = time.monotonic() + countdown
                    additional_message = f" for {countdown} seconds"
                self._logger.info(
                    f"Lights have been manually turned {mode}"
                    f"{additional_message}")
        else:
            raise RuntimeError(f"{self._subroutine_name} is not started in "
                               f"engine {self._ecosystem}")

    def get_countdown(self) -> float:
        return round(self._timer - time.monotonic(), 2)

    def increase_countdown(self, additional_time: float) -> None:
        if self._timer:
            self._logger.info(f"Increasing timer by {additional_time} seconds")
            self._timer += additional_time
        else:
            self._timer = time.monotonic() + additional_time

    def decrease_countdown(self, decrease_time: float) -> None:
        if self._timer:
            self._logger.info(f"Decreasing timer by {decrease_time} seconds")
            self._timer -= decrease_time
            if self._timer <= 0:
                self._timer = 0
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
