from functools import partial
from typing import Any, Callable, Coroutine, Type

from apscheduler.triggers.cron import CronTrigger

import gaia_validators as gv

from gaia.actuator_handler import ActuatorHandler, Timer
from gaia.hardware import actuator_models
from gaia.hardware.abc import Actuator
from gaia.subroutines.template import SubroutineTemplate


class Weather(SubroutineTemplate[Actuator]):
    _hardware_choices: dict[str, Type[Actuator]] = actuator_models

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Routine parameters
        self._active_parameters: dict[gv.WeatherParameter, str] = {}
        self._jobs: set[gv.WeatherParameter] = set()
        self._timers: dict[str, Timer] = {}

    async def _routine(self) -> None:
        # Weather uses multiple cron-scheduled jobs rather than a single routine
        raise ValueError

    def _compute_if_manageable(self) -> bool:
        if not self.compute_expected_actuators():
            self.logger.warning(
                "No parameters that could be regulated were found. Disabling "
                "Weather subroutine.")
            return False
        return True

    async def _start(self) -> None:
        self.logger.info(
            "Starting the weather subroutine. Its actions frequency are "
            "determined in the config file")
        expected_parameters = self.compute_expected_actuators().keys()
        for weather_parameter in expected_parameters:
            # Mount actuator handler
            await self._activate_actuator_handler(weather_parameter)
            # Add job
            await self._add_job(weather_parameter)

    async def _stop(self) -> None:
        # Close all jobs
        for weather_parameter in [*self._jobs]:
            await self._remove_job(weather_parameter)
        self._timers = {}
        # Deactivate activated actuator handlers
        for weather_parameter in [*self._active_parameters]:
            await self._inactivate_actuator_handler(weather_parameter)

    def get_hardware_needed_uid(self) -> set[str]:
        hardware_needed: set[str] = set()
        for actuator_group in self.compute_expected_actuators().values():
            extra = set(self.ecosystem.get_hardware_group_uids(actuator_group))
            hardware_needed = hardware_needed | extra
        return hardware_needed

    async def _refresh(self) -> None:
        # Remove all jobs to make sure they will be updated if they changed
        for weather_parameter in [*self._jobs]:
            await self._remove_job(weather_parameter)
        # Activate and deactivate actuator handlers if required
        expected = self.compute_expected_actuators()
        for weather_parameter, actuator_group in [*self._active_parameters.items()]:
            # Deactivate the parameters removed and the ones linked to another group
            if expected.get(weather_parameter) != actuator_group:
                await self._inactivate_actuator_handler(weather_parameter)
        for weather_parameter in expected.keys() - self._active_parameters.keys():
            await self._activate_actuator_handler(weather_parameter)
        # Reset actuator handlers
        for actuator_group in self._active_parameters.values():
            actuator_handler = self.get_actuator_handler(actuator_group)
            actuator_handler.reset_cached_actuators()
        # Add back jobs
        for weather_parameter in expected:
            await self._add_job(weather_parameter)

    """Routine specific methods"""
    def compute_expected_actuators(self) -> dict[gv.WeatherParameter, str]:
        """Return the actuator groups that should be mounted for the weather events

        The keys are the weather events and the values are the associated actuator
        groups"""
        rv: dict[gv.WeatherParameter, str] = {}
        weather_to_group = self.config.weather_direction_to_group
        for weather_event in self.config.weather:
            actuator_group = weather_to_group.get((weather_event, "increase"))
            if actuator_group is None:
                self.logger.debug(
                    f"Weather parameter '{weather_event}' has no linked "
                    f"actuator, it will not be managed.")
                continue
            # Make sure the actuator group is available
            if self.ecosystem.get_hardware_group_uids(actuator_group):
                rv[weather_event] = actuator_group
        return rv

    async def _activate_actuator_handler(self, weather_parameter: gv.WeatherParameter) -> None:
        if weather_parameter in self._active_parameters:
            raise ValueError(
                f"Actuator handler for weather parameter '{weather_parameter}' "
                f"is already mounted"
            )
        actuator_group = self.get_actuator_group_for_parameter(weather_parameter)
        actuator_handler = self.get_actuator_handler(actuator_group)
        async with actuator_handler.update_status_transaction(activation=True):
            actuator_handler.activate()
        actuator_handler.reset_cached_actuators()
        self._active_parameters[weather_parameter] = actuator_group

    async def _inactivate_actuator_handler(self, weather_parameter: gv.WeatherParameter) -> None:
        if weather_parameter not in self._active_parameters:
            raise ValueError(
                f"Actuator handler for weather parameter '{weather_parameter}' is "
                f"not activated"
            )
        # Use the group activated, the config may have changed since then
        actuator_group = self._active_parameters[weather_parameter]
        actuator_handler = self.get_actuator_handler(actuator_group)
        async with actuator_handler.update_status_transaction(activation=True):
            if actuator_handler.mode is gv.ActuatorMode.automatic:
                await actuator_handler.reset()
            actuator_handler.deactivate()
        del self._active_parameters[weather_parameter]

    def _create_job_func(
            self,
            job_name: str,
            actuator_handler: ActuatorHandler,
            duration: float,
            level: float,
    ) -> Callable[[], Coroutine[Any, Any, None]]:
        async def delayed_restoration(status, level, mode) -> None:
            self.logger.debug(
                f"Job for `{job_name}` weather event is over. Restoring actuator "
                f"handler to {status} {level} {mode}")
            async with actuator_handler.update_status_transaction():
                if mode is gv.ActuatorMode.automatic:
                    # Going back to automatic mode will apply the latest automatic
                    #  target requested by climate
                    await actuator_handler.set_mode(mode)
                else:
                    await actuator_handler.set_status(status)
                    await actuator_handler.set_level(level)
                    await actuator_handler.set_mode(mode)
            del self._timers[job_name]

        async def wrapper() -> None:
            self.logger.debug(
                f"Activating job for `{job_name}` weather event. Turning actuator "
                f"handler to {level}")

            current_status = actuator_handler.status
            current_level = actuator_handler.level
            current_mode = actuator_handler.mode

            async with actuator_handler.update_status_transaction():
                await actuator_handler.set_status(True)
                await actuator_handler.set_level(level)
                await actuator_handler.set_mode(gv.ActuatorMode.manual)
            callback = partial(delayed_restoration, current_status, current_level, current_mode)
            self._timers[job_name] = Timer(callback, duration)

        return wrapper

    async def _add_job(self, weather_parameter: gv.WeatherParameter) ->  None:
        if weather_parameter in self._jobs:
            raise ValueError(
                f"Job for weather parameter '{weather_parameter}' already exists"
            )
        weather_cfg = self.ecosystem.config.get_weather_config(weather_parameter)
        # Raises a `KeyError` if the actuator handler is not activated
        actuator_group = self._active_parameters[weather_parameter]
        actuator_handler = self.get_actuator_handler(actuator_group)
        # Add the job
        self.logger.debug(f"Creating job for `{weather_parameter}` weather event")
        self.ecosystem.engine.scheduler.add_job(
            func=self._create_job_func(
                weather_parameter, actuator_handler, weather_cfg.duration, weather_cfg.level),
            id=f"{self.ecosystem.uid}-weather_routine-{weather_parameter}",
            name=f"Weather job for {weather_parameter}",
            trigger=CronTrigger.from_crontab(weather_cfg.pattern),
        )
        self._jobs.add(weather_parameter)

    async def _remove_job(self, weather_parameter: gv.WeatherParameter) -> None:
        if weather_parameter not in self._jobs:
            raise ValueError(
                f"Job for weather parameter {weather_parameter} does not exist"
            )
        # Remove the job
        identifier = f"{self.ecosystem.uid}-weather_routine-{weather_parameter}"
        self.ecosystem.engine.scheduler.remove_job(identifier)
        self._jobs.remove(weather_parameter)
        # Remove the timer if it has been set
        if weather_parameter in self._timers:
            self._timers[weather_parameter].cancel()
            del self._timers[weather_parameter]

    def get_actuator_handler(self, actuator_group: str) -> ActuatorHandler:
        return self.ecosystem.actuator_hub.get_handler(actuator_group)

    def get_actuator_group_for_parameter(self, weather_parameter: gv.WeatherParameter) -> str:
        # Raises `UndefinedParameter` if the weather parameter is not configured
        weather_cfg = self.config.get_weather_config(weather_parameter)
        weather_to_group = self.config.weather_direction_to_group
        try:
            return weather_to_group[(weather_cfg.parameter, "increase")]
        except KeyError:
            raise ValueError(
                f"Weather parameter '{weather_parameter}' has no linked actuator."
            )
