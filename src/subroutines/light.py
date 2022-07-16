from datetime import date, datetime, time
from statistics import mean
from threading import Event, Lock, Thread
import time as ctime

from simple_pid import PID
from socketio.exceptions import BadNamespaceError

from ..exceptions import HardwareNotFound, UndefinedParameter
from ..hardware import ACTUATORS, I2C_LIGHT_SENSORS
from ..hardware.ABC import Switch
from ..subroutines.template import SubroutineTemplate
from config import Config


Kp = 0.05
Ki = 0.005
Kd = 0.01
lock = Lock()


def _to_dt(_time: time) -> datetime:
    # Transforms time to today's datetime. Needed to use timedelta
    _date = date.today()
    return datetime.combine(_date, _time)


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


class Light(SubroutineTemplate):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._status = {"current": False, "last": False}
        self._mode = "automatic"
        self._method = self.config.light_method
        self._dimmable_lights_uid = []
        self._pid = PID(Kp, Ki, Kd)
        self._sun_times = {"morning_start": time(8), "evening_end": time(20)}
        self._stop_event = Event()
        self._adjust_light_level_event = Event()
        self._timer: ctime.monotonic() = 0.0
        self._finish__init__()

    def _light_state_loop(self) -> None:
        self.logger.info(
            f"Starting light loop at a frequency of {1/Config.LIGHT_LOOP_PERIOD} Hz"
        )
        while not self._stop_event.is_set():
            if self._timer:
                if self._timer < ctime.monotonic():
                    self._timer = 0
                    self._mode = "automatic"
            self._light_state_routine()
            self._stop_event.wait(Config.LIGHT_LOOP_PERIOD)

    def _light_state_routine(self) -> None:
        # If lighting == True, lights should be on
        send_data = False
        if self._lighting:
            # If lights were closed, turn them on
            if not self._status["last"]:
                # Reset pid so there is no internal value overshoot
                self._status["current"] = True
                for light in self.hardware.values():
                    light.turn_on()
                if self._mode == "automatic":
                    self.logger.info("Lights have been automatically turned on")
                    send_data = True
        # If lighting == False, lights should be off
        else:
            # If lights were opened, turn them off
            if self._status["last"]:
                self._status["current"] = False
                for light in self.hardware.values():
                    light.turn_off()
                if self._mode == "automatic":
                    self.logger.info("Lights have been automatically turned off")
                    send_data = True
        if send_data and self.ecosystem.event_handler:
            try:
                self.ecosystem.event_handler.on_send_light_data(
                    ecosystem_uids=self.config.uid
                )
            except AttributeError as e:
                self.logger.error(e)
            except BadNamespaceError:
                self.logger.warning(
                    "Not connected to the server, cannot send "
                    "light info"
                )
        self._status["last"] = self._status["current"]

    # TODO: add a second loop for light level, only used if light is on and dimmable
    def _light_level_loop(self) -> None:
        sensor_subroutine = self.ecosystem.subroutines.get("sensor", None)
        light_sensors = []
        if sensor_subroutine:
            for sensor in sensor_subroutine.hardware.values():
                if sensor.model in I2C_LIGHT_SENSORS:
                    light_sensors.append(sensor)
        while not self._adjust_light_level_event.is_set():
            light_level = []
            for light_sensor in light_sensors:
                light_level.append(light_sensor._get_lux())
            mean_light = mean(light_level)
            self._light_level_routine(mean_light)
            self._adjust_light_level_event.wait(1)

    def _light_level_routine(self, light_level) -> None:
        pass

    """Functions to switch the light on/off either manually or automatically"""
    @property
    def _lighting(self) -> bool:
        if self._mode == "automatic":
            return self.expected_status
        else:  # self._mode == "manual"
            if self._status["current"]:
                return True
            else:
                return False

    def _update_manageable(self) -> None:
        if self.config.get_IO_group("light"):
            self.manageable = True
        else:
            self.logger.warning(
                "No light detected, disabling Light subroutine"
            )
            self.manageable = False

    def _start(self):
        # TODO: check that the ecosystem has day and night parameters
        now = datetime.now()
        if now.date() > self.ecosystem.engine.last_sun_times_update.date():
            self.ecosystem.engine.refresh_sun_times()
        self.update_sun_times(send=True)
        self.refresh_hardware()
        self._light_loop_thread = Thread(
            target=self._light_state_loop, args=()
        )
        self._light_loop_thread.name = f"{self._uid}-light_loop"
        self._light_loop_thread.start()

    def _stop(self):
        self.logger.info("Stopping light loop")
        self._stop_event.set()
        self._adjust_light_level_event.set()
        self._light_loop_thread.join()
        self.hardware = {}

    """API calls"""
    def add_hardware(self, hardware_dict: dict) -> Switch:
        hardware_uid = list(hardware_dict.keys())[0]
        try:
            hardware_dict[hardware_uid]["level"] = "environment"
            hardware = self._add_hardware(hardware_dict, ACTUATORS)
            hardware.turn_off()
            self.hardware[hardware_uid] = hardware
            if "dimmable" in hardware.model:
                self._dimmable_lights_uid.append(hardware_uid)
            self.logger.debug(f"Light '{hardware.name}' has been set up")
            return hardware
        except HardwareNotFound as e:
            self.logger.error(f"{e.__class__.__name__}: {e}")
        except KeyError as e:
            self.logger.error(
                f"Could not configure light {hardware_uid}, one of the "
                f"required info is missing. ERROR msg: {e}"
            )

    def remove_hardware(self, hardware_uid: str) -> None:
        try:
            if "dimmable" in self.hardware[hardware_uid].model:
                self._dimmable_lights_uid.remove(hardware_uid)
            del self.hardware[hardware_uid]
        except KeyError:
            self.logger.error(f"Light '{hardware_uid}' does not exist")

    def refresh_hardware(self) -> None:
        self._refresh_hardware("light")

    def update_sun_times(self, send=True) -> None:
        try:
            time_parameters = self.config.time_parameters
        except UndefinedParameter:
            time_parameters = {}
        try:
            sun_times = self.config.sun_times
        except UndefinedParameter:
            sun_times = {}
        # Check we've got the info required
        # Then update info using lock as the whole dict should be transformed at the "same time"
        if self._method == "fixed":
            if not time_parameters.get("day", False):
                self.logger.error(
                    "Cannot use method 'fixed' without time parameters set in "
                    "config. Turning out light"
                )
                self.stop()
            else:
                with lock:
                    self._sun_times["morning_start"] = time_parameters["day"]
                    self._sun_times["evening_end"] = time_parameters["night"]

        elif self._method == "place":
            if not sun_times.get("sunrise", False):
                self.logger.error(
                    "Cannot use method 'place' without sun times available. "
                    "Using 'fixed' method instead."
                )
                self.method = "fixed"
                self.update_sun_times()
            else:
                with lock:
                    self._sun_times["morning_start"] = sun_times["sunrise"]
                    self._sun_times["evening_end"] = sun_times["sunset"]

        elif self._method == "elongate":
            if not time_parameters.get("day", False) and not sun_times.get("sunrise", False):
                self.logger.error(
                    "Cannot use method 'elongate' without time parameters set in "
                    "config and sun times available. Using 'fixed' method instead."
                )
                self.method = "fixed"
                self.update_sun_times()
            else:
                sunrise = _to_dt(sun_times["sunrise"])
                sunset = _to_dt(sun_times["sunset"])
                twilight_begin = _to_dt(sun_times["twilight_begin"])
                offset = sunrise - twilight_begin
                with lock:
                    self._sun_times["morning_start"] = time_parameters["day"]
                    self._sun_times["morning_end"] = (sunrise + offset).time()
                    self._sun_times["evening_start"] = (sunset - offset).time()
                    self._sun_times["evening_end"] = time_parameters["night"]
        else:
            self.stop()

        if (
                self.config.get_management("climate") and
                self.ecosystem.subroutines.get("climate", False)
        ):
            try:
                self.ecosystem.subroutines["climate"].update_time_parameters()
            except Exception as e:
                self.logger.error(
                    f"Could not update climate routine times parameters. Error "
                    f"msg: {e}"
                )

        if self.ecosystem.event_handler and send:
            try:
                self.ecosystem.event_handler.on_send_light_data(
                    ecosystem_uids=(self._uid, )
                )
            except AttributeError as e:
                self.logger.error(e)
            except BadNamespaceError as e:
                self.logger.warning(
                    "Not connected to the server, cannot send light info"
                )

    @ property
    def expected_status(self) -> bool:
        now = datetime.now().time()
        if self._method == "elongate":
            # If time between lightning hours
            if (
                self._sun_times["morning_start"] <= now <= self._sun_times["morning_end"]
                or
                self._sun_times["evening_start"] <= now <= self._sun_times["evening_end"]
            ):
                return True
            else:
                return False
        else:
            return _is_time_between(
                self._sun_times["morning_start"],
                self._sun_times["evening_end"],
                check_time=now
            )

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
        return {
            "morning_start": self._sun_times["morning_start"],
            "morning_end": self._sun_times.get("morning_end", None),
            "evening_start": self._sun_times.get("evening_start", None),
            "evening_end": self._sun_times["evening_end"]
        }

    @property
    def timer(self) -> float:
        if self._timer:
            if self._timer > ctime.monotonic():
                return ctime.monotonic() - self._timer
        return 0.0

    @property
    def light_info(self) -> dict:
        return {
            **{
                "status": (
                    self.light_status if self.mode == "manual"
                    else self.expected_status
                ),
                "mode": self.mode,
                "method": self.method,
                "timer": self.timer,
            },
            **self.lighting_hours
        }

    def turn_light(self, mode="automatic", countdown: float = 0.0):
        if self._started:
            if mode == "automatic":
                self._mode = "automatic"
                self.logger.info("Lights have been turned to automatic mode")
            elif mode in ("on", "off"):
                self._mode = "manual"
                new_status = False
                if mode == "on":
                    new_status = True
                self._status["current"] = new_status
                additional_message = ""
                if countdown:
                    self._timer = ctime.monotonic() + countdown
                    additional_message = f" for {countdown} seconds"
                self.logger.info(
                    f"Lights have been manually turned {mode}"
                    f"{additional_message}")
        else:
            raise RuntimeError(f"{self.name} is not started in "
                               f"engine {self.ecosystem}")

    def get_countdown(self) -> float:
        return round(self._timer - ctime.monotonic(), 2)

    def increase_countdown(self, additional_time: float) -> None:
        if self._timer:
            self.logger.info(f"Increasing timer by {additional_time} seconds")
            self._timer += additional_time
        else:
            self._timer = ctime.monotonic() + additional_time

    def decrease_countdown(self, decrease_time: float) -> None:
        if self._timer:
            self.logger.info(f"Decreasing timer by {decrease_time} seconds")
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
