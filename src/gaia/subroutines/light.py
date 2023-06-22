from __future__ import annotations

from datetime import datetime, time
from statistics import mean
from threading import Event, Lock, Thread
import typing as t

from simple_pid import PID

from gaia_validators import (
    ActuatorModePayload, HardwareConfig, HardwareType, LightingHours,
    LightMethod)

from gaia.config import get_config
from gaia.exceptions import UndefinedParameter
from gaia.hardware import actuator_models
from gaia.hardware.abc import Dimmer, Hardware, LightSensor, Switch
from gaia.subroutines.actuator_handler import ActuatorHandler
from gaia.subroutines.template import SubroutineTemplate


Kp = 0.05
Ki = 0.005
Kd = 0.01
lock = Lock()


def _is_time_between(
        begin_time: time,
        end_time: time,
        check_time: time | None = None
) -> bool:
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
        self._light_loop_thread: Thread | None = None
        self.actuator: ActuatorHandler = ActuatorHandler(
            self, HardwareType.light, self.expected_status)
        self._last_light_status = self.actuator.status
        self._dimmers: set[str] = set()
        self._pid = PID(Kp, Ki, Kd)
        self._stop_event = Event()
        self._adjust_light_level_event = Event()
        self._finish__init__()

    @staticmethod
    def expected_status(
            *,
            method: LightMethod,
            lighting_hours: LightingHours
    ) -> bool:
        now: time = datetime.now().astimezone().time()
        if method == LightMethod.elongate:
            # If time between lightning hours
            if (
                lighting_hours.morning_start <= now <= lighting_hours.morning_end
                or
                lighting_hours.evening_start <= now <= lighting_hours.evening_end
            ):
                return True
            else:
                return False
        else:
            return _is_time_between(
                lighting_hours.morning_start,
                lighting_hours.evening_end,
                check_time=now
            )

    def _light_state_loop(self) -> None:
        cfg = get_config()
        self.logger.info(
            f"Starting light loop at a frequency of {1/cfg.LIGHT_LOOP_PERIOD} Hz")
        while not self._stop_event.is_set():
            self._light_state_routine()
            self._stop_event.wait(cfg.LIGHT_LOOP_PERIOD)

    def _light_state_routine(self) -> None:
        # If lighting == True, lights should be on
        lighting = self.actuator.compute_expected_status(
            method=self.ecosystem.light_method, lighting_hours=self.lighting_hours)
        if lighting:
            # Reset pid so there is no internal value overshoot
            if not self.actuator.last_status:
                self._pid.reset()
            self.actuator.status = True
            for light in self.hardware.values():
                light.turn_on()
        # If lighting == False, lights should be off
        else:
            self.actuator.status = False
            for light in self.hardware.values():
                light.turn_off()
        self._last_light_status = self.actuator.status

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
                    light = light_sensor.get_lux()
                    if light is not None:
                        light_level.append(light)
                mean_light = mean(light_level)
                self._light_level_routine(mean_light)
                self._adjust_light_level_event.wait(1)

    def _light_level_routine(self, light_level: float) -> None:
        pass

    """Functions to switch the light on/off either manually or automatically"""
    def _update_manageable(self) -> None:
        try:
            time_parameters = bool(self.config.time_parameters)
        except UndefinedParameter:
            time_parameters = False
        if all((
                self.config.get_IO_group_uids("light"),
                self.ecosystem.light_method,
                time_parameters
        )):
            self.manageable = True
        else:
            self.logger.warning(
                "At least one of light hardware, lighting method, or time "
                "parameters is missing. Disabling Light subroutine"
            )
            self.manageable = False

    def _start(self):
        self.light_loop_thread = Thread(
            target=self._light_state_loop, args=())
        self.light_loop_thread.name = f"{self._uid}-light_loop"
        self.light_loop_thread.start()
        self.actuator.active = True

    def _stop(self):
        self.logger.info("Stopping light loop")
        self._stop_event.set()
        self._adjust_light_level_event.set()
        self.light_loop_thread.join()
        self.actuator.active = False
        self.hardware = {}

    """API calls"""
    @property
    def light_loop_thread(self) -> Thread:
        if self._light_loop_thread is None:
            raise RuntimeError("Thread has not been set up")
        else:
            return self._light_loop_thread

    @light_loop_thread.setter
    def light_loop_thread(self, thread: Thread | None):
        self._light_loop_thread = thread

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

    @property
    def lighting_hours(self) -> LightingHours:
        return self.ecosystem.lighting_hours

    def turn_light(
            self,
            turn_to: ActuatorModePayload = ActuatorModePayload.automatic,
            countdown: float = 0.0
    ) -> None:
        if self._started:
            self.actuator.turn_to(turn_to, countdown)
        else:
            raise RuntimeError(
                f"{self.name} is not started in engine {self.ecosystem}")

    @property
    def PID_tunings(self) -> tuple:
        """Returns the tunings used by the controller as a tuple: (Kp, Ki, Kd)"""
        return self._pid.tunings

    @PID_tunings.setter
    def PID_tunings(self, tunings: tuple) -> None:
        """:param tunings: tuple (Kp, Ki, Kd)"""
        self._pid.tunings = tunings
