from __future__ import annotations

from datetime import datetime, time
from statistics import mean
import typing

from apscheduler.triggers.interval import IntervalTrigger

import gaia_validators as gv

from gaia.actuator_handler import HystericalPID
from gaia.hardware import actuator_models
from gaia.hardware.abc import Dimmer, Hardware, LightSensor, Switch
from gaia.subroutines.template import SubroutineTemplate


if typing.TYPE_CHECKING:
    from gaia.actuator_handler import ActuatorHandler


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
        self._loop_period: float = float(
            self.ecosystem.engine.config.app_config.LIGHT_LOOP_PERIOD)
        self._light_sensors: list[LightSensor] | None = None
        self._any_dimmable_light: bool | None = None
        self._light_method: gv.LightMethod | None = None  # For tests only
        self._lighting_hours: gv.LightingHours | None = None  # For test only
        self._finish__init__()

    def _safe_light_routine(self) -> None:
        try:
            self._light_routine()
        except Exception as e:
            self.logger.error(
                f"Encountered an error while running the light routine. "
                f"ERROR msg: `{e.__class__.__name__} :{e}`."
            )

    def _light_routine(self) -> None:
        pid: HystericalPID = self.get_pid()
        target, hysteresis = self.compute_target()
        pid.target = target
        pid.hysteresis = hysteresis

        current_value: float | None = self.get_ambient_light_level()
        if current_value is None:
            current_value = 0.0

        pid_output = pid.update_pid(current_value)
        expected_status = self.actuator_handler.compute_expected_status(pid_output)

        if expected_status:
            self.actuator_handler.turn_on()
            self.actuator_handler.set_level(pid_output)
        else:
            self.actuator_handler.turn_off()
            self.actuator_handler.set_level(0.0)

    """Functions to switch the light on/off either manually or automatically"""
    def _compute_if_manageable(self) -> bool:
        if all((
                self.config.get_IO_group_uids(gv.HardwareType.light),
                self.light_method,
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
        pid = self.get_pid()
        pid.reset()
        self.logger.info(
            f"Starting the light loop. It will run every "
            f"{self._loop_period:.2f} s.")
        self.ecosystem.engine.scheduler.add_job(
            func=self._safe_light_routine,
            id=f"{self.ecosystem.uid}-light_routine",
            trigger=IntervalTrigger(seconds=self._loop_period, jitter=self._loop_period/20),
        )
        self.actuator_handler.activate()

    def _stop(self) -> None:
        self.logger.info("Stopping light loop")
        self.ecosystem.engine.scheduler.remove_job(f"{self.ecosystem.uid}-light_routine")
        self.actuator_handler.deactivate()

    """API calls"""
    def add_hardware(self, hardware_config: gv.HardwareConfig) -> Switch | Dimmer:
        hardware = super().add_hardware(hardware_config)
        self.reset_any_dimmable_light()
        return hardware

    def remove_hardware(self, hardware_uid: str) -> None:
        super().remove_hardware(hardware_uid)
        self.reset_any_dimmable_light()

    def get_hardware_needed_uid(self) -> set[str]:
        return set(self.config.get_IO_group_uids(gv.HardwareType.light))

    @property
    def actuator_handler(self) -> ActuatorHandler:
        return self.ecosystem.actuator_hub.get_handler(gv.HardwareType.light)

    def get_pid(self) -> HystericalPID:
        return self.ecosystem.actuator_hub.get_pid(gv.ClimateParameter.light)

    @property
    def light_method(self) -> gv.LightMethod:
        if self._light_method is None:
            return self.ecosystem.light_method
        return self._light_method

    @light_method.setter
    def light_method(self, light_method: gv.LightMethod) -> None:
        if not self.ecosystem.engine.config.app_config.TESTING:
            raise AttributeError("'light_method' can only be set when 'TESTING' is True")
        self._light_method = light_method

    @property
    def lighting_hours(self) -> gv.LightingHours:
        if self._lighting_hours is None:
            return self.ecosystem.lighting_hours
        return self._lighting_hours

    @lighting_hours.setter
    def lighting_hours(self, lighting_hours: gv.LightingHours) -> None:
        if not self.ecosystem.engine.config.app_config.TESTING:
            raise AttributeError("'lighting_hours' can only be set when 'TESTING' is True")
        self._lighting_hours = lighting_hours

    @property
    def light_sensors(self) -> list[LightSensor]:
        if self._light_sensors is None:
            self._light_sensors = [
                hardware for hardware in Hardware.get_mounted().values()
                if hardware.ecosystem_uid == self.ecosystem.uid
                and isinstance(hardware, LightSensor)
            ]
        return self._light_sensors

    def reset_light_sensors(self) -> None:
        self._light_sensors = None

    @property
    def any_dimmable_light(self) -> bool:
        if self._any_dimmable_light is None:
            for hardware in self.hardware.values():
                if isinstance(hardware, Dimmer):
                    self._any_dimmable_light = True
            if self._any_dimmable_light is None:
                self._any_dimmable_light = False
        return self._any_dimmable_light

    def reset_any_dimmable_light(self) -> None:
        self._any_dimmable_light = None

    def get_ambient_light_level(self) -> float | None:
        # If there isn't any light sensors we cannot get the info
        # If there isn't any dimmable light, the info cannot be properly used
        if not self.light_sensors or not self.any_dimmable_light:
            return None
        light_level: list[float] = []
        for light_sensor in self.light_sensors:
            light = light_sensor.get_lux()
            if light is not None:
                light_level.append(light)
        if not light_level:
            return None
        return mean(light_level)

    def compute_status(self, _now: time | None = None) -> bool:
        now: time = _now or datetime.now().astimezone().time()
        if self.light_method == gv.LightMethod.elongate:
            # If time between lightning hours
            if (
                self.lighting_hours.morning_start <= now <= self.lighting_hours.morning_end
                or
                self.lighting_hours.evening_start <= now <= self.lighting_hours.evening_end
            ):
                return True
            else:
                return False
        else:
            return _is_time_between(
                self.lighting_hours.morning_start,
                self.lighting_hours.evening_end,
                check_time=now
            )

    def compute_level(self,  _now: time | None = None) -> float:
        if not self.light_sensors or not self.any_dimmable_light:
            return 50_000.0
        else:
            # TODO: use a function with sharper rise and fall than sin and a plateau
            return 50_000.0

    def compute_target(self, _now: time | None = None) -> tuple[float, None]:
        now: time = _now or datetime.now().astimezone().time()

        status = self.compute_status(now)

        if not status:
            return -30_000.0, None  # To be sure that the PID output always will be < 0

        level = self.compute_level(now)
        return level, None

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
