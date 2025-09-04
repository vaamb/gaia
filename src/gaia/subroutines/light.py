from __future__ import annotations

import asyncio
from asyncio import Task
from datetime import datetime, time
from statistics import mean
from time import monotonic
import typing

import gaia_validators as gv

from gaia.actuator_handler import HystericalPID
from gaia.exceptions import UndefinedParameter
from gaia.hardware import actuator_models
from gaia.hardware.abc import Dimmer, Hardware, LightSensor, Switch
from gaia.subroutines.template import SubroutineTemplate
from gaia.utils import is_time_between


if typing.TYPE_CHECKING:
    from gaia.actuator_handler import ActuatorHandler


DEFAULT_CLIMATE_CFG = gv.ClimateConfig(**{
    "parameter": gv.ClimateParameter.light,
    "day": 250_000,
    "night": -30_000,
    "hysteresis": 0.0,
})


class Light(SubroutineTemplate[Switch]):
    def __init__(self, *args, **kwargs) -> None:
        # Parent template
        super().__init__(*args, **kwargs)
        self.hardware_choices = actuator_models
        # Subroutine specific
        self._light_sensors: list[LightSensor] | None = None
        self._any_dimmable_light: bool | None = None
        # Actuator handler
        self._actuator_handler: ActuatorHandler | None = None
        self._pid: HystericalPID | None = None
        # Background task
        self._loop_period: float = float(
            self.ecosystem.engine.config.app_config.LIGHT_LOOP_PERIOD)
        self._task: Task | None = None
        self._finish__init__()

    """SubroutineTemplate methods"""
    async def _routine(self) -> None:
        try:
            await self._update_light_actuators()
        except Exception as e:
            self.logger.error(
                f"Encountered an error while running the light routine. "
                f"ERROR msg: `{e.__class__.__name__} :{e}`."
            )

    async def routine_task(self) -> None:
        while True:
            start = monotonic()
            await self.routine()
            sleep_time = max(self._loop_period - (monotonic() - start), 0.01)
            await asyncio.sleep(sleep_time)

    def _compute_if_manageable(self) -> bool:
        if all((
                self.config.get_IO_group_uids(gv.HardwareType.light),
                bool(self.config.lighting_hours.morning_start),
        )):
            return True
        else:
            self.logger.warning(
                "At least one of light hardware, lighting method, or time "
                "parameters is missing."
            )
            return False

    async def _start(self) -> None:
        # Initialize actuator handler and PID
        self._actuator_handler = self.get_actuator_handler()
        self._pid = self.get_pid()
        self.pid.reset()
        # Activate actuator handler
        async with self.actuator_handler.update_status_transaction(activation=True):
            self.actuator_handler.activate()
        # Start light routine
        self.logger.info(
            f"Starting the light loop. It will run every "
            f"{self._loop_period:.2f} s.")
        self._task = asyncio.create_task(
            self.routine_task(), name=f"{self.ecosystem.uid}-light-routine")

    async def _stop(self) -> None:
        self.logger.info("Stopping light loop.")
        # Stop light routine
        self._task.cancel()
        self._task = None
        # Deactivate actuator handler
        async with self.actuator_handler.update_status_transaction(activation=True):
            self.actuator_handler.deactivate()
        # Reset actuator handler and PID
        self._actuator_handler = None
        self._pid = None

    def get_hardware_needed_uid(self) -> set[str]:
        return set(self.config.get_IO_group_uids(gv.HardwareType.light))

    async def refresh(self) -> None:
        await super().refresh()
        # Make sure the routine is still running
        if not self.started:
            return
        # Make sure PID is in sync with actuator handler
        assert self._pid is not None
        # No need to activate or deactivate actuator handler: it is done in
        #  during start and stop and if there isn't linked actuator, the subroutine
        #  will stop.
        # Reset actuator handler, PID and light sensors
        self.actuator_handler.reset_cached_actuators()
        self.pid.reset()
        self.reset_light_sensors()
        self.reset_any_dimmable_light()

    """Routine specific methods"""
    def get_actuator_handler(self) -> ActuatorHandler:
        return self.ecosystem.actuator_hub.get_handler(gv.HardwareType.light)

    @property
    def actuator_handler(self) -> ActuatorHandler:
        if self._actuator_handler is None:
            raise ValueError(
                "actuator_handler is not defined in non-started Light subroutine")
        return self._actuator_handler

    def get_pid(self) -> HystericalPID:
        return self.ecosystem.actuator_hub.get_pid(gv.ClimateParameter.light)

    @property
    def pid(self) -> HystericalPID:
        if self._pid is None:
            raise ValueError(
                "pid is not defined in non-started Light subroutine")
        return self._pid

    @property
    def light_sensors(self) -> list[LightSensor]:
        if self._light_sensors is None:
            self._light_sensors = [
                hardware
                for hardware in self.ecosystem.hardware.values()
                if isinstance(hardware, LightSensor)
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

    async def _get_ambient_light_level(self) -> float:
        # If there isn't any light sensors we cannot get the info
        # If there isn't any dimmable light, the info cannot be properly used
        if not self.light_sensors or not self.any_dimmable_light:
            return 0.0  # Fallback value
        futures = [
            asyncio.create_task(light_sensor.get_lux())
            for light_sensor in self.light_sensors
        ]
        if not futures:
            return 0.0  # Fallback value
        done, pending = await asyncio.wait(futures, timeout=self._loop_period / 2)
        for future in pending:
            future.cancel()
        light_level: list[float] = [future.result() for future in done]
        return mean(light_level)

    async def _update_pid(self) -> None:
        pid: HystericalPID = self.get_pid()
        target, hysteresis = self.compute_target()
        pid.target = target
        pid.hysteresis = hysteresis
        current_value: float = await self._get_ambient_light_level()
        pid.update_pid(current_value)

    async def _update_actuator_handler(self, actuator_handler: ActuatorHandler) -> None:
        pid = actuator_handler.get_associated_pid()
        expected_status = actuator_handler.compute_expected_status(pid.last_output)
        if expected_status:
            await actuator_handler.turn_on()
            await actuator_handler.set_level(pid.last_output)
        else:
            await actuator_handler.turn_off()
            await actuator_handler.set_level(0.0)

    async def _update_light_actuators(self) -> None:
        await self._update_pid()
        async with self.actuator_handler.update_status_transaction():
            await self._update_actuator_handler(self.actuator_handler)

    """API calls"""
    def _compute_target_status(self, _now: time | None = None) -> bool:
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

    def compute_target(self, _now: time | None = None) -> tuple[float, float]:
        try:
            climate_cfg = self.config.get_climate_parameter(gv.ClimateParameter.light)
        except UndefinedParameter:
            climate_cfg = DEFAULT_CLIMATE_CFG
        chaos_factor = self.config.get_chaos_factor()
        now = _now or datetime.now().time()
        target_status = self._compute_target_status(now)
        if target_status:
            target = climate_cfg.day * chaos_factor
        else:
            target = climate_cfg.night * chaos_factor
        hysteresis = climate_cfg.hysteresis * chaos_factor
        return target, hysteresis

    async def turn_light(
            self,
            turn_to: gv.ActuatorModePayload = gv.ActuatorModePayload.automatic,
            countdown: float = 0.0
    ) -> None:
        if not self._started:
            raise RuntimeError("Light subroutine is not started")
        async with self.actuator_handler.update_status_transaction():
            await self.actuator_handler.turn_to(turn_to, countdown)
