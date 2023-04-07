from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, time
from statistics import mean
from threading import Event, Lock, Thread
import time as ctime
import typing as t

from simple_pid import PID

from gaia_validators import (
    ActuatorMode, ActuatorTurnTo, HardwareConfig, HardwareType,
    LightData, LightingHours, LightMethod, SunTimes
)

from gaia.config import get_config
from gaia.exceptions import StoppingSubroutine, UndefinedParameter
from gaia.hardware import actuator_models
from gaia.hardware.abc import Dimmer, Hardware, LightSensor, Switch
from gaia.subroutines.template import SubroutineTemplate


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.subroutines.climate import Climate


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
    check_time = check_time or datetime.now().astimezone().time()
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
        self.hardware: dict[str, "Switch"]
        self._status = {"current": False, "last": False}
        self._mode: ActuatorMode = ActuatorMode.automatic
        self._dimmers: set[str] = set()
        self._pid = PID(Kp, Ki, Kd)
        self._lighting_hours: LightingHours = LightingHours()
        self._stop_event = Event()
        self._adjust_light_level_event = Event()
        self._timer: ctime.monotonic() = 0.0
        self._method: LightMethod | None
        try:
            self._method = self.config.light_method
        except UndefinedParameter:
            self._method = None
        self._finish__init__()

    def _refresh_lighting_hours(self, send=True) -> None:
        if self._method is None:
            self.logger.error("Lighting method is not defined")
            return

        self.logger.debug("Updating sun times")
        time_parameters = self.config.time_parameters
        sun_times: SunTimes | None
        try:
            sun_times = self.config.sun_times
        except UndefinedParameter:
            sun_times = None
        # Check we've got the info required
        # Then update info using lock as the whole dict should be transformed at the "same time"
        if self._method == LightMethod.fixed:
            if time_parameters.day is None or time_parameters.night is None:
                self.logger.error(
                    "Cannot use method 'fixed' without time parameters set in "
                    "config. Turning out light"
                )
                raise StoppingSubroutine
            else:
                with lock:
                    self._lighting_hours = LightingHours(
                        morning_start=time_parameters.day,
                        evening_end=time_parameters.night,
                    )

        elif self._method == LightMethod.mimic:
            if sun_times is None:
                self.logger.error(
                    "Cannot use method 'place' without sun times available. "
                    "Using 'fixed' method instead."
                )
                self.method = LightMethod.fixed
                self._refresh_lighting_hours()
            else:
                with lock:
                    self._lighting_hours = LightingHours(
                        morning_start=sun_times.sunrise,
                        evening_end=sun_times.sunset,
                    )

        elif self._method == LightMethod.elongate:
            if (
                    time_parameters.day is None
                    or time_parameters.night is None
                    or sun_times is None
            ):
                self.logger.error(
                    "Cannot use method 'elongate' without time parameters set in "
                    "config and sun times available. Using 'fixed' method instead."
                )
                self.method = LightMethod.fixed
                self._refresh_lighting_hours()
            else:
                sunrise = _to_dt(sun_times.sunrise)
                sunset = _to_dt(sun_times.sunset)
                twilight_begin = _to_dt(sun_times.twilight_begin)
                offset = sunrise - twilight_begin
                with lock:
                    self._lighting_hours = LightingHours(
                        morning_start=time_parameters.day,
                        morning_end=(sunrise + offset).time(),
                        evening_start=(sunset - offset).time(),
                        evening_end=time_parameters.night,
                    )

        else:
            raise StoppingSubroutine

        if self.ecosystem.get_subroutine_status("climate"):
            climate_subroutine: Climate = self.ecosystem.subroutines["climate"]
            try:
                climate_subroutine.update_time_parameters()
            except Exception as e:
                self.logger.error(
                    f"Could not update climate subroutine times parameters. "
                    f"ERROR msg: `{e.__class__.__name__} :{e}`."
                )

        if self.ecosystem.event_handler and send:
            try:
                self.ecosystem.event_handler.send_light_data(
                    ecosystem_uids=[self._uid]
                )
            except Exception as e:
                msg = e.args[1] if len(e.args) > 1 else e.args[0]
                if "is not a connected namespace" in msg:
                    return  # TODO: find a way to catch if many errors
                self.logger.error(
                    f"Encountered an error while sending light data. "
                    f"ERROR msg: `{e.__class__.__name__} :{e}`"
                )

    def _light_state_loop(self) -> None:
        cfg = get_config()
        self.logger.info(
            f"Starting light loop at a frequency of {1/cfg.LIGHT_LOOP_PERIOD} Hz"
        )
        while not self._stop_event.is_set():
            if self._timer:
                if self._timer < ctime.monotonic():
                    self._timer = 0
                    self._mode = ActuatorMode.automatic
            self._light_state_routine()
            self._stop_event.wait(cfg.LIGHT_LOOP_PERIOD)

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
                if self._mode == ActuatorMode.automatic:
                    self.logger.info("Lights have been automatically turned on")
                    send_data = True
        # If lighting == False, lights should be off
        else:
            # If lights were opened, turn them off
            if self._status["last"]:
                self._status["current"] = False
                for light in self.hardware.values():
                    light.turn_off()
                if self._mode == ActuatorMode.automatic:
                    self.logger.info("Lights have been automatically turned off")
                    send_data = True
        if send_data and self.ecosystem.event_handler:
            try:
                self.ecosystem.event_handler.send_light_data(
                    ecosystem_uids=self.config.uid
                )
            except Exception as e:
                msg = e.args[1] if len(e.args) > 1 else e.args[0]
                if "is not a connected namespace" in msg:
                    pass
                self.logger.error(
                    f"Encountered an error while sending light data. "
                    f"ERROR msg: `{e.__class__.__name__} :{e}`"
                )
        self._status["last"] = self._status["current"]

    # TODO: add a second loop for light level, only used if light is on and dimmable
    def _light_level_loop(self) -> None:
        if self.ecosystem.get_subroutine_status("sensors"):
            while not self._adjust_light_level_event.is_set():
                light_sensors: list[LightSensor] = [
                    sensor for sensor in
                    Hardware.get_actives_by_type(HardwareType.sensor)
                    if isinstance(sensor, LightSensor)
                ]
                light_level: list[float] = []
                for light_sensor in light_sensors:
                    light = light_sensor._get_lux()
                    if light is not None:
                        light_level.append(light)
                mean_light = mean(light_level)
                self._light_level_routine(mean_light)
                self._adjust_light_level_event.wait(1)

    def _light_level_routine(self, light_level: float) -> None:
        pass

    """Functions to switch the light on/off either manually or automatically"""
    @property
    def _lighting(self) -> bool:
        if self._mode is ActuatorMode.automatic:
            return self.expected_status
        else:
            if self._status["current"]:
                return True
            else:
                return False

    def _update_manageable(self) -> None:
        try:
            time_parameters = bool(self.config.time_parameters)
        except UndefinedParameter:
            time_parameters = False
        if all((self.config.get_IO_group_uids("light"), self.method, time_parameters)):
            self.manageable = True
        else:
            self.logger.warning(
                "At least one of light hardware, lighting method, or time "
                "parameters is missing. Disabling Light subroutine"
            )
            self.manageable = False

    def _start(self):
        now = datetime.now().astimezone()
        if now.date() > self.ecosystem.config.general.last_sun_times_update.date():
            self.ecosystem.engine.refresh_sun_times()
        self._refresh_lighting_hours(send=True)
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
    def add_hardware(self, hardware_config: HardwareConfig):
        hardware: Switch = self._add_hardware(hardware_config, actuator_models)
        if isinstance(hardware, Dimmer):
            self._dimmers.add(hardware.uid)

    def remove_hardware(self, hardware_uid: str) -> None:
        try:
            if isinstance(self.hardware[hardware_uid], Dimmer):
                self._dimmers.remove(hardware_uid)
            del self.hardware[hardware_uid]
        except KeyError:
            self.logger.error(f"Light '{hardware_uid}' does not exist")

    def get_hardware_needed_uid(self) -> set[str]:
        return set(self.config.get_IO_group_uids("light"))

    def refresh_sun_times(self, send=True):
        try:
            self._refresh_lighting_hours(send)
        except StoppingSubroutine:
            self.stop()

    @ property
    def expected_status(self) -> bool:
        now = datetime.now().astimezone().time()
        if self._method == "elongate":
            # If time between lightning hours
            if (
                self._lighting_hours.morning_start <= now <= self._lighting_hours.morning_end
                or
                self._lighting_hours.evening_start <= now <= self._lighting_hours.evening_end
            ):
                return True
            else:
                return False
        else:
            return _is_time_between(
                self._lighting_hours.morning_start,
                self._lighting_hours.evening_end,
                check_time=now
            )

    @property
    def light_status(self) -> bool:
        return self._status["current"]

    @property
    def mode(self) -> ActuatorMode:
        return self._mode

    @mode.setter
    def mode(self, value: ActuatorMode) -> None:
        self._mode = value

    @property
    def method(self) -> LightMethod:
        return self._method

    @method.setter
    def method(self, value: LightMethod) -> None:
        self._method = value
        if value in (LightMethod.elongate, LightMethod.mimic):
            self.refresh_sun_times(send=True)

    @property
    def lighting_hours(self) -> LightingHours:
        return self._lighting_hours

    @property
    def timer(self) -> float:
        if self._timer:
            if self._timer > ctime.monotonic():
                return ctime.monotonic() - self._timer
        return 0.0

    @property
    def light_info(self) -> LightData:
        if self.mode is ActuatorMode.automatic:
            status = self.expected_status
        else:
            status = self.light_status
        return LightData(
            status=status,
            mode=self.mode,
            method=self.method,
            timer=self.timer,
            **asdict(self.lighting_hours)
        )

    def turn_light(
            self,
            turn_to: ActuatorTurnTo = "automatic",
            countdown: float = 0.0
    ) -> None:
        if self._started:
            if turn_to == "automatic":
                self.mode = ActuatorMode.automatic
                self.logger.info("Lights have been turned to automatic mode")
            else:
                self.mode = ActuatorMode.manual
                if turn_to == "on":
                    self._status["current"] = True
                else:
                    self._status["current"] = False
            additional_message = ""
            if countdown:
                self._timer = ctime.monotonic() + countdown
                additional_message = f" for {countdown} seconds"
            self.logger.info(
                f"Lights have been manually turned {turn_to}"
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
