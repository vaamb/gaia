from __future__ import annotations

from datetime import datetime, time
from statistics import mean
from threading import Event, Thread
import typing

from simple_pid import PID

import gaia_validators as gv

from gaia.hardware import actuator_models
from gaia.hardware.abc import Dimmer, Hardware, LightSensor, Switch
from gaia.subroutines.template import SubroutineTemplate


if typing.TYPE_CHECKING:
    from gaia.actuator_handler import ActuatorHandler


Kp = 0.05
Ki = 0.005
Kd = 0.01


# TODO: improve
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
        self.hardware_choices = actuator_models
        self.hardware: dict[str, "Switch"]
        self._light_status_thread: Thread | None = None
        self._light_intensity_thread: Thread | None = None
        self._dimmers: set[str] = set()
        self._pid = PID(Kp, Ki, Kd)
        self._stop_event = Event()
        self._finish__init__()

    @staticmethod
    def expected_status(
            *,
            method: gv.LightMethod,
            lighting_hours: gv.LightingHours,
            _now: time | None = None,
    ) -> bool:
        now: time = _now or datetime.now().astimezone().time()
        if method == gv.LightMethod.elongate:
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

    @property

    def actuator_handler(self) -> ActuatorHandler:
        return self.ecosystem.actuator_handlers.get_handler(gv.HardwareType.light)

    def _light_status_loop(self) -> None:
        cfg = self.ecosystem.engine.config.app_config
        self.logger.info(
            f"Starting light loop at a frequency of {1/cfg.LIGHT_LOOP_PERIOD} Hz")
        while not self._stop_event.is_set():
            self._light_status_routine()
            self._stop_event.wait(cfg.LIGHT_LOOP_PERIOD)

    def _light_status_routine(self) -> None:
        # If lighting == True, lights should be on
        lighting = self.actuator_handler.compute_expected_status(
            method=self.ecosystem.light_method,
            lighting_hours=self.lighting_hours,
        )
        if lighting:
            # Reset pid so there is no internal value overshoot
            if not self.actuator_handler.last_status:
                self._pid.reset()
            self.actuator_handler.set_status(True)
        # If lighting == False, lights should be off
        else:
            self.actuator_handler.set_status(False)

    # TODO: add a second loop for light level, only used if light is on and dimmable
    def _light_intensity_loop(self) -> None:
        if self.ecosystem.get_subroutine_status("sensors"):
            while not self._stop_event.is_set():
                light_sensors: list[LightSensor] = [
                    hardware for hardware in Hardware.get_mounted().values()
                    if hardware.ecosystem_uid == self.ecosystem.uid
                    and isinstance(hardware, LightSensor)
                ]
                light_level: list[float] = []
                for light_sensor in light_sensors:
                    light = light_sensor.get_lux()
                    if light is not None:
                        light_level.append(light)
                mean_light = mean(light_level)
                self._light_intensity_routine(mean_light)
                self._stop_event.wait(1)

    def _light_intensity_routine(self, light_level: float) -> None:
        pass

    """Functions to switch the light on/off either manually or automatically"""
    def _compute_if_manageable(self) -> bool:
        if all((
                self.config.get_IO_group_uids("light"),
                self.ecosystem.light_method,
                bool(self.lighting_hours.morning_start)
        )):
            return True
        else:
            self.logger.warning(
                "At least one of light hardware, lighting method, or time "
                "parameters is missing."
            )
            return False

    def _start(self) -> None:
        self._stop_event.clear()
        self.light_status_thread = Thread(
            target=self._light_status_loop,
            name=f"{self.ecosystem.uid}-light-status")
        self.light_status_thread.start()
        # self.light_intensity_thread = Thread(
        #     target=self._light_intensity_loop,
        #     name=f"{self._uid}-light-intensity")
        # self.light_intensity_thread.start()
        self.actuator_handler.activate()

    def _stop(self) -> None:
        self.logger.info("Stopping light loop")
        self._stop_event.set()
        self.light_status_thread.join()
        self.light_status_thread = None
        # self.light_intensity_thread.join()
        # self.light_intensity_thread = None
        self.actuator_handler.deactivate()

    """API calls"""
    @property
    def light_status_thread(self) -> Thread:
        if self._light_status_thread is None:
            raise AttributeError("Light status thread has not been set up")
        else:
            return self._light_status_thread

    @light_status_thread.setter
    def light_status_thread(self, thread: Thread | None) -> None:
        self._light_status_thread = thread

    @property
    def light_intensity_thread(self) -> Thread:
        if self._light_intensity_thread is None:
            raise AttributeError("Light intensity thread has not been set up")
        else:
            return self._light_intensity_thread

    @light_intensity_thread.setter
    def light_intensity_thread(self, thread: Thread | None) -> None:
        self._light_intensity_thread = thread

    def add_hardware(self, hardware_config: gv.HardwareConfig) -> Hardware:
        hardware = super().add_hardware(hardware_config)
        if isinstance(hardware, Dimmer):
            self._dimmers.add(hardware.uid)

    def remove_hardware(self, hardware_uid: str) -> None:
        super().remove_hardware(hardware_uid)
        if hardware_uid in self._dimmers:
            self._dimmers.remove(hardware_uid)

    def get_hardware_needed_uid(self) -> set[str]:
        return set(self.config.get_IO_group_uids("light"))

    @property
    def lighting_hours(self) -> gv.LightingHours:
        return self.ecosystem.lighting_hours

    def turn_light(
            self,
            turn_to: gv.ActuatorModePayload = gv.ActuatorModePayload.automatic,
            countdown: float = 0.0
    ) -> None:
        if self._started:
            self.actuator_handler.turn_to(turn_to, countdown)
        else:
            raise RuntimeError(
                f"Light subroutine is not started in ecosystem {self.ecosystem}")

    @property
    def PID_tunings(self) -> tuple[float, float, float]:
        """Returns the tunings used by the controller as a tuple: (Kp, Ki, Kd)"""
        return self._pid.tunings

    @PID_tunings.setter
    def PID_tunings(self, tunings: tuple[float, float, float]) -> None:
        """:param tunings: tuple (Kp, Ki, Kd)"""
        self._pid.tunings = tunings
        self._pid.reset()
