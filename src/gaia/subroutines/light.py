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
from gaia.utils import is_time_between


if typing.TYPE_CHECKING:
    from gaia.actuator_handler import ActuatorHandler


class Light(SubroutineTemplate):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hardware_choices = actuator_models
        self.hardware: dict[str, "Switch"]
        self._loop_period: float = float(
            self.ecosystem.engine.config.app_config.LIGHT_LOOP_PERIOD)
        self._light_sensors: list[LightSensor] | None = None
        self._any_dimmable_light: bool | None = None
        self._finish__init__()

    async def routine(self) -> None:
        try:
            await self._update_light_actuators()
        except Exception as e:
            self.logger.error(
                f"Encountered an error while running the light routine. "
                f"ERROR msg: `{e.__class__.__name__} :{e}`."
            )

    async def _update_light_actuators(self) -> None:
        pid: HystericalPID = self.get_pid()
        target, hysteresis = self.compute_target()
        pid.target = target
        pid.hysteresis = hysteresis

        current_value: float | None = await self.get_ambient_light_level()
        if current_value is None:
            current_value = 0.0

        pid_output = pid.update_pid(current_value)
        await self.actuator_handler.check_countdown()
        expected_status = self.actuator_handler.compute_expected_status(pid_output)

        if expected_status:
            await self.actuator_handler.turn_on()
            await self.actuator_handler.set_level(pid_output)
        else:
            await self.actuator_handler.turn_off()
            await self.actuator_handler.set_level(0.0)

    """Functions to switch the light on/off either manually or automatically"""
    def _compute_if_manageable(self) -> bool:
        if all((
                self.config.get_IO_group_uids(gv.HardwareType.light),
                bool(self.config.lighting_hours.morning_start)
        )):
            return True
        else:
            self.logger.warning(
                "At least one of light hardware, lighting method, or time "
                "parameters is missing."
            )
            return False

    async def _start(self) -> None:
        pid = self.get_pid()
        pid.reset()
        self.logger.info(
            f"Starting the light loop. It will run every "
            f"{self._loop_period:.2f} s.")
        self.ecosystem.engine.scheduler.add_job(
            func=self.routine,
            id=f"{self.ecosystem.uid}-light_routine",
            trigger=IntervalTrigger(seconds=self._loop_period, jitter=self._loop_period/20),
        )
        self.actuator_handler.activate()

    async def _stop(self) -> None:
        self.logger.info("Stopping light loop")
        self.ecosystem.engine.scheduler.remove_job(f"{self.ecosystem.uid}-light_routine")
        self.actuator_handler.deactivate()

    """API calls"""
    async def add_hardware(self, hardware_config: gv.HardwareConfig) -> Switch | Dimmer:
        hardware = await super().add_hardware(hardware_config)
        self.reset_any_dimmable_light()
        return hardware

    async def remove_hardware(self, hardware_uid: str) -> None:
        await super().remove_hardware(hardware_uid)
        self.reset_any_dimmable_light()

    def get_hardware_needed_uid(self) -> set[str]:
        return set(self.config.get_IO_group_uids(gv.HardwareType.light))

    async def refresh_hardware(self) -> None:
        await super().refresh_hardware()
        actuator_handler = self.ecosystem.actuator_hub.get_handler(gv.HardwareType.light)
        actuator_handler.reset_cached_actuators()

    @property
    def actuator_handler(self) -> ActuatorHandler:
        return self.ecosystem.actuator_hub.get_handler(gv.HardwareType.light)

    def get_pid(self) -> HystericalPID:
        return self.ecosystem.actuator_hub.get_pid(gv.ClimateParameter.light)

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
                    break
            if self._any_dimmable_light is None:
                self._any_dimmable_light = False
        return self._any_dimmable_light

    def reset_any_dimmable_light(self) -> None:
        self._any_dimmable_light = None

    async def get_ambient_light_level(self) -> float | None:
        # If there isn't any light sensors we cannot get the info
        # If there isn't any dimmable light, the info cannot be properly used
        if not self.light_sensors or not self.any_dimmable_light:
            return None
        light_level: list[float] = []
        for light_sensor in self.light_sensors:
            light = await light_sensor.get_lux()
            if light is not None:
                light_level.append(light)
        if not light_level:
            return None
        return mean(light_level)

    def compute_status(self, _now: time | None = None) -> bool:
        now = _now or datetime.now().time()
        hours = self.config.lighting_hours
        if self.config.lighting_method == gv.LightMethod.elongate:
            # Is time between lightning hours
            if (
                hours.morning_start <= now <= hours.morning_end
                or hours.evening_start <= now <= hours.evening_end
            ):
                return True
            else:
                return False
        else:
            return is_time_between(hours.morning_start, hours.evening_end, now)

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

    async def turn_light(
            self,
            turn_to: gv.ActuatorModePayload = gv.ActuatorModePayload.automatic,
            countdown: float = 0.0
    ) -> None:
        if self._started:
            await self.actuator_handler.turn_to(turn_to, countdown)
        else:
            raise RuntimeError(
                f"Light subroutine is not started in ecosystem {self.ecosystem}")
