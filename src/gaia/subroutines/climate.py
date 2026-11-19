from __future__ import annotations

from time import monotonic
import typing as t
from typing import Type

import gaia_validators as gv

from gaia.actuator_handler import HystericalPID
from gaia.config.defaults import Direction, ClimateDirection
from gaia.hardware import actuator_models
from gaia.hardware.abc import Actuator
from gaia.subroutines.template import SubroutineTemplate


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.actuator_handler import ActuatorHandler
    from gaia.subroutines.sensors import Sensors


MISSES_BEFORE_STOP = 5


class Climate(SubroutineTemplate[Actuator]):
    _hardware_choices: dict[str, Type[Actuator]] = actuator_models

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Routine parameters
        loop_period = float(self.ecosystem.engine.config.app_config.CLIMATE_LOOP_PERIOD)
        self._loop_period: float = max(loop_period, 10.0)
        self._actuator_handlers: dict[ClimateDirection, ActuatorHandler] | None = None
        # The handlers' mode after the last loop, so we can reset the PID when it
        #  changes
        self._handler_modes: dict[ClimateDirection, gv.ActuatorMode] = {}
        self._pids: dict[gv.ClimateParameter, HystericalPID] | None = None
        self._sensor_miss: int = 0

    """SubroutineTemplate methods"""
    async def _routine(self) -> None:
        start_time = monotonic()
        try:
            await self._update_climate_actuators()
        except Exception as e:
            self.logger.error(
                f"Encountered an error while running the climate routine. "
                f"ERROR msg: `{e.__class__.__name__}: {e}`."
            )
        loop_time = monotonic() - start_time
        if loop_time > self._loop_period:  # pragma: no cover
            self.logger.warning(
                f"Climate routine took {loop_time:.1f}. You should consider "
                f"increasing 'CLIMATE_LOOP_PERIOD'."
            )

    def _compute_if_manageable(self) -> bool:
        if not self.compute_expected_actuators():
            self.logger.warning(
                "No parameters that could be regulated were found. "
                "Disabling Climate subroutine."
            )
            return False
        else:
            return True

    async def _start(self) -> None:
        self.logger.info(
            f"Starting the climate loop. It will run every "
            f"{self._loop_period:.1f} s.")
        climate_directions = self.compute_expected_actuators().keys()
        # Mount PID controllers
        climate_parameters = set(cd[0] for cd in climate_directions)
        self._pids = {}
        for climate_parameter in climate_parameters:
            self._add_pid(climate_parameter)
        # Mount required actuator handlers
        self._actuator_handlers = {}
        for climate_direction in climate_directions:
            await self._mount_actuator_handler(climate_direction)

    async def _stop(self) -> None:
        # Deactivate activated actuator handlers
        for climate_direction in [*self.actuator_handlers.keys()]:
            await self._unmount_actuator_handler(climate_direction)
        self._actuator_handlers = None
        # Remove PIDs
        for climate_parameter in [*self.pids.keys()]:
            self._remove_pid(climate_parameter)
        self._pids = None

    def get_hardware_needed_uid(self) -> set[str]:
        hardware_needed: set[str] = set()
        for actuator_group in self.compute_expected_actuators().values():
            extra = set(self.ecosystem.get_hardware_group_uids(actuator_group))
            hardware_needed = hardware_needed | extra
        return hardware_needed

    async def _refresh(self) -> None:
        climate_directions = self.compute_expected_actuators().keys()
        assert self._pids is not None
        pids_expected: set[gv.ClimateParameter] = set(cd[0] for cd in climate_directions)
        pids_mounted: set[gv.ClimateParameter] = set(self.pids)
        # Activate PIDs that are needed before actuators handlers using them are 
        #  mounted
        for climate_parameter in pids_expected - pids_mounted:
            self._add_pid(climate_parameter)
        # Activate, deactivate and reset actuator handlers if required
        handlers_expected: set[ClimateDirection] = set(climate_directions)
        handlers_mounted: set[ClimateDirection] = set(self.actuator_handlers.keys())
        for climate_direction in handlers_expected - handlers_mounted:
            await self._mount_actuator_handler(climate_direction)
        for climate_direction in handlers_mounted - handlers_expected:
            await self._unmount_actuator_handler(climate_direction)
        for actuator_handler in self.actuator_handlers.values():
            actuator_handler.reset_cached_actuators()
        # Deactivate PIDs not needed anymore and reset them
        for parameter in pids_mounted - pids_expected:
            self._remove_pid(parameter)
        for pid in self.pids.values():
            pid.reset()
            pid.reset_direction()

    """Routine specific methods"""
    def get_actuator_handler(self, actuator_group: str) -> ActuatorHandler:
        return self.ecosystem.actuator_hub.get_handler(actuator_group)

    @property
    def actuator_handlers(self) -> dict[ClimateDirection, ActuatorHandler]:
        """Return the actuator handlers used by the climate subroutine.

        The result is a dictionary where the keys are a tuple of the climate
        parameters and the associated direction, and the values are the
        associated actuator handlers.
        """
        if self._actuator_handlers is None:
            raise ValueError(
                "actuator_handlers is not defined in non-started Climate subroutine")
        return self._actuator_handlers

    def get_actuator_group_for_parameter(self, climate_direction: ClimateDirection) -> str:
        return self.ecosystem.config.climate_direction_to_group[climate_direction]

    async def _mount_actuator_handler(self, climate_direction: ClimateDirection) -> None:
        if climate_direction in self.actuator_handlers:
            raise ValueError(
                f"Actuator handler for climate parameter {climate_direction[0]} "
                f"is already mounted"
            )
        actuator_group = self.get_actuator_group_for_parameter(climate_direction)
        actuator_handler = self.get_actuator_handler(actuator_group)
        self.actuator_handlers[climate_direction] = actuator_handler
        self._handler_modes[climate_direction] = actuator_handler.mode
        async with actuator_handler.update_status_transaction(activation=True):
            actuator_handler.activate()
        actuator_handler.reset_cached_actuators()
        # The PID's directions now include this handler's
        climate_parameter = climate_direction[0]
        self.pids[climate_parameter].reset_direction()

    async def _unmount_actuator_handler(self, climate_direction: ClimateDirection) -> None:
        if climate_direction not in self.actuator_handlers:
            raise ValueError(
                f"Actuator handler for climate parameter {climate_direction[0]} "
                f"is not mounted"
            )
        actuator_handler = self.actuator_handlers[climate_direction]
        async with actuator_handler.update_status_transaction(activation=True):
            if actuator_handler.mode is gv.ActuatorMode.automatic:
                await actuator_handler.reset()
            actuator_handler.deactivate()
        del self.actuator_handlers[climate_direction]
        del self._handler_modes[climate_direction]

    def _add_pid(self, climate_parameter: gv.ClimateParameter) -> None:
        pid = self.ecosystem.actuator_hub.get_pid(climate_parameter)
        pid.reset()
        self.pids[climate_parameter] = pid

    def _remove_pid(self, climate_parameter: gv.ClimateParameter) -> None:
        del self.pids[climate_parameter]
        if not self.pids:
            self._pids = None

    @property
    def pids(self) -> dict[gv.ClimateParameter, HystericalPID]:
        if self._pids is None:
            raise ValueError(
                "pids is not defined in non-started Climate subroutine")
        return self._pids

    # Climate parameters and actuators management
    def compute_expected_actuators(self) -> dict[ClimateDirection, str]:
        """Return the actuator groups that should be mounted for the climate events

        The result is a dictionary where the keys are a tuple of the climate
        parameters and the associated direction, and the values are the
        associated actuator groups."""
        # Make sure the sensor subroutine is running
        if not self.ecosystem.get_subroutine_status("sensors"):
            self.logger.warning(
                "Climate subroutine requires a running sensors subroutine in "
                "order to work.")
            return {}

        # Check if climate parameters are available in the config file
        regulated_parameters: list[gv.ClimateParameter] = [
            climate_parameter for climate_parameter in self.config.climate.keys()
            if climate_parameter != "light"
        ]
        if not regulated_parameters:
            self.logger.warning("No climate parameter found.")
            return {}

        # Get mounted sensors and the measures they're taking
        measures: set[str] = {
            measure.name
            for sensor in self.ecosystem.hardware.values()
            for measure in sensor.measures
        }

        # Check if sensors taking regulated params are available
        for climate_param in [*regulated_parameters]:
            measure = self._get_measure_for_parameter(climate_param)
            if measure not in measures:
                regulated_parameters.remove(climate_param)
        if not regulated_parameters:
            self.logger.debug("No sensor measuring regulated parameters detected.")
            return {}

        # Check if there are actuator groups available and map them with climate parameters
        rv: dict[ClimateDirection, str] = {}
        climate_to_group = self.config.climate_direction_to_group
        for climate_param in regulated_parameters:
            for direction in ("increase", "decrease"):
                direction: Direction
                try:
                    actuator_group = climate_to_group[(climate_param, direction)]
                except KeyError:
                    continue
                if self.ecosystem.get_hardware_group_uids(actuator_group):
                    rv[(climate_param, direction)] = actuator_group
        if not rv:
            self.logger.debug("No climatic actuator detected.")
            return {}
        return rv

    @property
    def regulated_parameters(self) -> list[gv.ClimateParameter]:
        if not self.started:
            return []
        expected_actuators = self.compute_expected_actuators()
        return [*set([
            climate_direction[0] for climate_direction
            in expected_actuators.keys()
        ])]

    # Routine specific methods
    def _check_misses(self) -> bool:
        if self._sensor_miss >= MISSES_BEFORE_STOP:
            self.logger.error(
                "Maximum number of Sensors data miss reached, stopping "
                "climate subroutine."
            )
            return True
        return False

    def _get_measure_for_parameter(self, parameter: gv.ClimateParameter) -> str:
        climate_cfg = self.config.get_climate_config(parameter)
        return (
            climate_cfg.linked_measure
            if climate_cfg.linked_measure else parameter
        )

    async def _get_sensors_average(self) -> dict[str, float]:
        # Get the sensors average
        sensors_subroutine: Sensors = self.ecosystem.get_subroutine("sensors")
        sensors_data = sensors_subroutine.sensors_data

        if isinstance(sensors_data, gv.Empty):
            self.logger.debug(
                f"No sensor data found, climate subroutine will try again "
                f"{MISSES_BEFORE_STOP - self._sensor_miss} times before "
                f"stopping.")
            self._sensor_miss += 1
            return {}

        sensors_average: dict[str, float] = {
            data.measure: data.value
            for data in sensors_data.average
        }

        # Make sure we have sensors data for all the regulated parameters
        missing_parameter: bool = False
        for climate_parameter in self.regulated_parameters:
            measure = self._get_measure_for_parameter(climate_parameter)
            if measure not in sensors_average:
                missing_parameter = True
                self.logger.debug(
                    f"No sensor data found for {climate_parameter}, climate "
                    f"subroutine will try again "
                    f"{MISSES_BEFORE_STOP - self._sensor_miss} times before "
                    f"adjusting its regulated parameters.")
        if missing_parameter:
            self._sensor_miss += 1
        else:
            # Reset sensor miss counter if there wasn't any new miss
            self._sensor_miss = 0

        return sensors_average

    def _update_pid(
            self,
            climate_parameter: gv.ClimateParameter,
            sensors_average: dict[str, float],
    ) -> None:
        pid: HystericalPID = self.pids[climate_parameter]
        scaled_target = self.config.get_scaled_climate_target(climate_parameter)
        target = scaled_target.day if self.config.is_day() else scaled_target.night
        hysteresis = scaled_target.hysteresis
        pid.target = target
        pid.hysteresis = hysteresis
        measure = self._get_measure_for_parameter(climate_parameter)
        current_value: float | None = sensors_average.get(measure)
        pid.update_pid(current_value)

    def _reset_pids_on_mode_change(self) -> None:
        # Reset the PIDs when they go from manual to automatic mode as the errors 
        #  they registered while overridden are meaningless
        for climate_direction, actuator_handler in self.actuator_handlers.items():
            if actuator_handler.mode is not self._handler_modes[climate_direction]:
                climate_parameter = climate_direction[0]
                self.pids[climate_parameter].reset()
                self._handler_modes[climate_direction] = actuator_handler.mode

    async def _update_actuator_handler(
            self,
            climate_direction: ClimateDirection,
            actuator_handler: ActuatorHandler,
    ) -> None:
        parameter, direction = climate_direction
        output = self.pids[parameter].last_output
        # Should be "on" when the output and the handler's direction go to the
        # same side
        status = output > 0.0 if direction == "increase" else output < 0.0
        level = abs(output) if status else 0.0
        await actuator_handler.set_automatic_target(status, level)

    async def _update_climate_actuators(self) -> None:
        sensors_average: dict[str, float] = await self._get_sensors_average()
        if self._check_misses():
            await self.stop()
            return
        self._reset_pids_on_mode_change()
        for climate_parameter in self.pids.keys():
            self._update_pid(climate_parameter, sensors_average)
        for climate_direction, actuator_handler in self.actuator_handlers.items():
            async with actuator_handler.update_status_transaction():
                await self._update_actuator_handler(climate_direction, actuator_handler)

    """API calls"""
    async def turn_climate_actuator(
        self,
        actuator_group: str,
        turn_to: gv.ActuatorModePayload = gv.ActuatorModePayload.automatic,
        level: float = 100.0,
        countdown: float = 0.0,
    ) -> None:
        if not self._started:
            raise RuntimeError("Climate subroutine is not started")
        await self.ecosystem.turn_actuator(
            actuator_group, turn_to, level=level, countdown=countdown)
