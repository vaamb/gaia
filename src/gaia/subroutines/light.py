from __future__ import annotations

import asyncio
from asyncio import CancelledError, Task
from statistics import mean
from time import monotonic
import typing
from typing import Type

import gaia_validators as gv

from gaia.actuator_handler import HystericalPID
from gaia.hardware import actuator_models
from gaia.hardware.abc import Actuator, DimmerMixin, LightSensorMixin
from gaia.subroutines.template import SubroutineTemplate


if typing.TYPE_CHECKING:
    from gaia.actuator_handler import ActuatorHandler


class Light(SubroutineTemplate[Actuator]):
    _hardware_choices: dict[str, Type[Actuator]] = actuator_models

    def __init__(self, *args, **kwargs) -> None:
        # Parent template
        super().__init__(*args, **kwargs)
        # Subroutine specific
        self._light_sensors: list[LightSensorMixin] | None = None
        self._any_dimmable_light: bool | None = None
        # Actuator handler
        self._actuator_group: str | None = None
        self._pid: HystericalPID | None = None
        # The handler's mode at the last loop, to reset the PID when it changes
        self._handler_mode: gv.ActuatorMode | None = None
        # Background task
        self._loop_period: float = float(
            self.ecosystem.engine.config.app_config.LIGHT_LOOP_PERIOD)
        # Every in-flight sensor read, whoever started it: a read must not be
        #  forgotten when another routine call starts its own
        self._get_lux_futures: set[Task] = set()
        self._task: Task | None = None

    """SubroutineTemplate methods"""
    async def _routine(self) -> None:
        try:
            await self._update_light_actuators()
        except Exception as e:
            self.logger.error(
                f"Encountered an error while running the light routine. "
                f"ERROR msg: `{e.__class__.__name__}: {e}`."
            )

    async def routine_task(self) -> None:
        while True:
            start = monotonic()
            try:
                await self.routine()
            except Exception as e:
                # Log without re-raising so a transient error does not
                # permanently kill the light routine task
                self.logger.error(
                    f"Encountered an error in the light routine task. "
                    f"ERROR msg: `{e.__class__.__name__}: {e}`."
                )
            sleep_time = max(self._loop_period - (monotonic() - start), 0.01)
            await asyncio.sleep(sleep_time)

    def _compute_if_manageable(self) -> bool:
        if all((
                self.ecosystem.get_hardware_group_uids(self._get_actuator_group()),
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
        # Initialize PID and actuator handler
        self._add_pid()
        await self._activate_actuator_handler(self._get_actuator_group())
        self._handler_mode = self.actuator_handler.mode
        # Start light routine
        self.logger.info(
            f"Starting the light loop. It will run every "
            f"{self._loop_period:.2f} s.")
        self._task = asyncio.create_task(
            self.routine_task(), name=f"{self.ecosystem.uid}-light-routine")

    async def _stop(self) -> None:
        self.logger.info("Stopping light loop.")
        # Stop light routine
        assert self._task is not None
        self._task.cancel()
        self._task = None
        # Cancel any in-flight sensor reads so they don't outlive the subroutine
        futures = [*self._get_lux_futures]
        for future in futures:
            future.cancel()
        if futures:
            try:
                await asyncio.gather(*futures)
            except CancelledError:
                # Futures created via `anyio.run_sync()` raise `CancelledError`
                #  when a parent task is cancelled
                pass
        # Reset light sensors
        self.reset_light_sensors()
        # Deactivate actuator handler
        await self._inactivate_actuator_handler()
        # Reset actuator handler and PID
        self._handler_mode = None
        self._remove_pid()

    def get_hardware_needed_uid(self) -> set[str]:
        actuator_group: str = self._get_actuator_group()
        return set(self.ecosystem.get_hardware_group_uids(actuator_group))

    async def _refresh(self) -> None:
        # Make sure PID is in sync with actuator handler
        assert self._pid is not None
        # Refresh the actuator handler in case the actuator group name has changed
        new_actuator_group = self._get_actuator_group()
        if self._actuator_group != new_actuator_group:
            await self._inactivate_actuator_handler()
            await self._activate_actuator_handler(new_actuator_group)
        self._handler_mode = self.actuator_handler.mode
        # Reset PID and light sensors
        self.pid.reset()
        self.pid.reset_direction()
        self.reset_light_sensors()
        self.reset_any_dimmable_light()

    """Routine specific methods"""
    def _get_actuator_group(self) -> str:
        climate_to_group = self.config.climate_direction_to_group
        return climate_to_group[("light", "increase")]

    def get_actuator_handler(self, actuator_group: str) -> ActuatorHandler:
        return self.ecosystem.actuator_hub.get_handler(actuator_group)

    @property
    def actuator_handler(self) -> ActuatorHandler:
        if self._actuator_group is None:
            raise ValueError(
                "actuator_handler is not defined in non-started Light subroutine")
        return self.get_actuator_handler(self._actuator_group)

    def _add_pid(self) -> None:
        pid = self.ecosystem.actuator_hub.get_pid("light")
        pid.reset()
        self._pid = pid

    def _remove_pid(self) -> None:
        self._pid = None

    @property
    def pid(self) -> HystericalPID:
        if self._pid is None:
            raise ValueError(
                "pid is not defined in non-started Light subroutine")
        return self._pid

    @property
    def light_sensors(self) -> list[LightSensorMixin]:
        if self._light_sensors is None:
            self._light_sensors = [
                hardware
                for hardware in self.ecosystem.hardware.values()
                if isinstance(hardware, LightSensorMixin)
            ]
        return self._light_sensors

    def reset_light_sensors(self) -> None:
        self._light_sensors = None

    @property
    def any_dimmable_light(self) -> bool:
        if self._any_dimmable_light is None:
            for hardware in self.hardware.values():
                if isinstance(hardware, DimmerMixin):
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
        for future in futures:
            self._get_lux_futures.add(future)
            future.add_done_callback(self._get_lux_futures.discard)
        done, pending = await asyncio.wait(futures, timeout=self._loop_period / 2)
        for future in pending:
            future.cancel()
        results: list[float | None] = [future.result() for future in done]
        light_level: list[float] = [result for result in results if result is not None]
        if len(light_level) == 0:
            return 0.0  # Fallback value
        return mean(light_level)

    async def _update_pid(self) -> None:
        pid: HystericalPID = self.pid
        scaled_target = self.config.get_scaled_climate_target("light")
        target = scaled_target.day if self.config.is_lighting_needed() else scaled_target.night
        hysteresis = scaled_target.hysteresis
        pid.target = target
        pid.hysteresis = hysteresis
        current_value: float = await self._get_ambient_light_level()
        pid.update_pid(current_value)

    async def _activate_actuator_handler(self, actuator_group: str) -> None:
        assert self._actuator_group is None
        actuator_handler = self.get_actuator_handler(actuator_group)
        async with actuator_handler.update_status_transaction(activation=True):
            actuator_handler.activate()
        actuator_handler.reset_cached_actuators()
        self._actuator_group = actuator_group

    async def _inactivate_actuator_handler(self) -> None:
        async with self.actuator_handler.update_status_transaction(activation=True):
            if self.actuator_handler.mode is gv.ActuatorMode.automatic:
                await self.actuator_handler.reset()
            self.actuator_handler.deactivate()
        self._actuator_group = None

    async def _update_actuator_handler(self, actuator_handler: ActuatorHandler) -> None:
        output = self.pid.last_output
        # Lights can only increase the light level
        status = output > 0.0
        level = output if status else 0.0
        await actuator_handler.set_automatic_target(status, level)

    def _reset_pid_on_mode_change(self, actuator_handler: ActuatorHandler) -> None:
        # The PID's history is meaningless once the lights were driven by
        #  someone else: reset it when they go from or back to automatic mode
        if actuator_handler.mode is not self._handler_mode:
            self._handler_mode = actuator_handler.mode
            self.pid.reset()

    async def _update_light_actuators(self) -> None:
        actuator_handler = self.actuator_handler
        self._reset_pid_on_mode_change(actuator_handler)
        await self._update_pid()
        async with actuator_handler.update_status_transaction():
            await self._update_actuator_handler(actuator_handler)

    """API calls"""
    async def turn_light(
            self,
            turn_to: gv.ActuatorModePayload = gv.ActuatorModePayload.automatic,
            level: float = 100.0,
            countdown: float = 0.0,
    ) -> None:
        if not self._started:
            raise RuntimeError("Light subroutine is not started")
        assert self._actuator_group is not None
        await self.ecosystem.turn_actuator(self._actuator_group,
            turn_to, level=level, countdown=countdown)
