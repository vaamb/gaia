from typing import Coroutine

from apscheduler.triggers.cron import CronTrigger

import gaia_validators as gv

from gaia.actuator_handler import ActuatorHandler, Timer
from gaia.hardware import actuator_models
from gaia.hardware.abc import Dimmer, Switch
from gaia.subroutines.template import SubroutineTemplate


class Weather(SubroutineTemplate[Dimmer | Switch]):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hardware_choices = actuator_models
        # Routine parameters
        self._actuator_handlers: dict[str, ActuatorHandler] | None = None
        self._jobs = set()
        self._timers: dict[str, Timer] = {}

    async def _routine(self) -> None:
        # Weather uses multiple cron-scheduled jobs rather than a single routine
        raise ValueError

    def _compute_if_manageable(self) -> bool:
        events = self._get_expected_weather_events()
        if not events:
            return False
        for event in events:
            weather_cfg = self.ecosystem.config.get_weather_parameter(event)
            actuator_group = weather_cfg.linked_actuator or weather_cfg.parameter
            if not self.ecosystem.get_hardware_group_uids(actuator_group):
                return False
        return True

    async def _start(self) -> None:
        self.logger.info(
            "Starting the weather subroutine. Its actions frequency are "
            "determined in the config file")
        self._actuator_handlers = {}
        expected_events = self._get_expected_weather_events()
        for event in expected_events:
            # Mount actuator handler
            await self._mount_actuator_handler(event)
            # Add job
            await self._add_job(event)

    async def _stop(self) -> None:
        # Deactivate activated actuator handlers
        for actuator_group in [*self.actuator_handlers.keys()]:
            await self._unmount_actuator_handler(actuator_group)
        # Reset actuator handlers
        self._actuator_handlers = None
        # Close all jobs
        for job in [*self._jobs]:
            await self._remove_job(job)
        self._timers = None

    def get_hardware_needed_uid(self) -> set[str]:
        hardware_needed: set[str] = set()
        for event in self._get_expected_weather_events():
            actuator_group = self.get_actuator_group_for_parameter(event)
            extra = set(self.ecosystem.get_hardware_group_uids(actuator_group))
            hardware_needed = hardware_needed | extra
        return hardware_needed

    async def refresh(self) -> None:
        # Refresh hardware
        await super().refresh()
        # Make sure the routine is still running
        if not self.started:
            return
        # Remove all jobs to make sure they will be updated if they changed
        for job in self._jobs:
            await self._remove_job(job)
        # Mount and unmount actuator handlers if required
        currently_expected: set[str] = set(self._get_expected_weather_events())
        currently_mounted: set[str] = set(self.actuator_handlers.keys())
        for actuator_group in currently_expected - currently_mounted:
            await self._mount_actuator_handler(actuator_group)
        for actuator_group in currently_mounted - currently_expected:
            await self._unmount_actuator_handler(actuator_group)
        # Add back jobs
        for job in currently_expected:
            await self._add_job(job)

    """Routine specific methods"""
    def _get_expected_weather_events(self) -> list[str]:
        return [*self.config.weather.keys()]

    async def _mount_actuator_handler(self, parameter: str) -> None:
        if parameter in self.actuator_handlers:
            raise ValueError(
                f"Actuator handler for weather parameter {parameter} is already mounted"
            )
        actuator_group = self.get_actuator_group_for_parameter(parameter)
        actuator_handler = self.get_actuator_handler(actuator_group)
        self.actuator_handlers[parameter] = actuator_handler
        async with actuator_handler.update_status_transaction(activation=True):
            actuator_handler.activate()
        actuator_handler.reset_cached_actuators()

    async def _unmount_actuator_handler(self, parameter: str) -> None:
        if parameter not in self.actuator_handlers:
            raise ValueError(
                f"Actuator handler for weather parameter {parameter} is not mounted"
            )
        actuator_handler = self.actuator_handlers[parameter]
        async with actuator_handler.update_status_transaction(activation=True):
            if actuator_handler.mode is gv.ActuatorMode.automatic:
                await actuator_handler.reset()
            actuator_handler.deactivate()
        del self.actuator_handlers[parameter]

    def _create_job_func(
            self,
            job_name: str,
            actuator_handler: ActuatorHandler,
            duration: float,
            level: float,
    ) -> Coroutine:
        mode = actuator_handler.mode
        status = actuator_handler.status
        level = actuator_handler.level

        async def delayed_restoration() -> None:
            async with actuator_handler.update_status_transaction():
                await actuator_handler.set_status(status)
                await actuator_handler.set_level(level)
                await actuator_handler.set_mode(mode)
            del self._timers[job_name]

        async def wrapper():
            async with actuator_handler.update_status_transaction():
                await actuator_handler.turn_to(gv.ActuatorModePayload.on, level=level)
            self._timers[job_name] = Timer(delayed_restoration, duration)

        return wrapper

    async def _add_job(self, parameter: str) ->  None:
        if parameter in self._jobs:
            raise ValueError(f"Job for weather parameter {parameter} already exists")
        weather_cfg = self.ecosystem.config.get_weather_parameter(parameter)
        # Should raise if the actuator handler is not mounted
        actuator_handler = self.actuator_handlers[parameter]
        # Add the job
        self.ecosystem.engine.scheduler.add_job(
            func=self._create_job_func(
                parameter, actuator_handler, weather_cfg.duration, weather_cfg.level),
            id=parameter,
            name=f"Weather job for {parameter}",
            trigger=CronTrigger.from_crontab(weather_cfg.pattern),
        )
        self._jobs.add(parameter)

    async def _remove_job(self, parameter: str) -> None:
        if parameter not in self._jobs:
            raise ValueError(f"Job for weather parameter {parameter} does not exist")
        # Remove the job
        self.ecosystem.engine.scheduler.remove_job(parameter)
        self._jobs.remove(parameter)
        # Remove the timer if it has been set
        if parameter in self._timers:
            del self._timers[parameter]

    def get_actuator_handler(self, actuator_group: str) -> ActuatorHandler:
        return self.ecosystem.actuator_hub.get_handler(actuator_group)

    @property
    def actuator_handlers(self) -> dict[str, ActuatorHandler]:
        """Return the actuator handlers used by the weather subroutine.

        The result is a dictionary where the keys are the weather event names
        and the values are the associated actuator handlers.
        """
        if self._actuator_handlers is None:
            raise ValueError(
                "actuator_handlers is not defined in non-started Climate subroutine")
        return self._actuator_handlers

    def get_actuator_group_for_parameter(self, parameter: str) -> str:
        weather_cfg = self.ecosystem.config.get_weather_parameter(parameter)
        return weather_cfg.linked_actuator or weather_cfg.parameter
