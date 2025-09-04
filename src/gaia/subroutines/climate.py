from __future__ import annotations

from datetime import datetime, time
from time import monotonic
import typing as t
from typing import Sequence

import gaia_validators as gv

from gaia.actuator_handler import (
    ActuatorCouple, actuator_couples, actuator_to_parameter, HystericalPID)
from gaia.exceptions import UndefinedParameter
from gaia.hardware import actuator_models
from gaia.hardware.abc import BaseSensor, Dimmer, Switch
from gaia.subroutines.template import SubroutineTemplate


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.actuator_handler import ActuatorHandler
    from gaia.subroutines.sensors import Sensors


MISSES_BEFORE_STOP = 5


class Climate(SubroutineTemplate[Dimmer | Switch]):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hardware_choices = actuator_models
        # Routine parameters
        loop_period = float(self.ecosystem.engine.config.app_config.CLIMATE_LOOP_PERIOD)
        self._loop_period: float = max(loop_period, 10.0)
        self._actuator_handlers: dict[gv.HardwareType, ActuatorHandler] | None = None
        self._pids: dict[gv.ClimateParameter, HystericalPID] | None = None
        self._activated_actuators: set[gv.HardwareType] = set()
        self._sensor_miss: int = 0
        self._finish__init__()

    """SubroutineTemplate methods"""
    async def _routine(self) -> None:
        start_time = monotonic()
        try:
            await self._update_climate_actuators()
        except Exception as e:
            self.logger.error(
                f"Encountered an error while running the climate routine. "
                f"ERROR msg: `{e.__class__.__name__} :{e}`."
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
        # Actuator activation is done during hardware refresh
        self.logger.info(
            f"Starting the climate loop. It will run every "
            f"{self._loop_period:.1f} s.")
        # Mount actuator handlers
        self._actuator_handlers = {}
        controllable_parameters = set()
        for actuator_type in gv.HardwareType.climate_actuator:
            controllable_parameters.add(actuator_to_parameter[actuator_type])
            actuator_handler = self.get_actuator_handler(actuator_type)
            self.actuator_handlers[actuator_type] = actuator_handler
        # Activate the required actuators
        expected_actuators = self.compute_expected_actuators()
        for actuator_type in expected_actuators:
            actuator_handler = self.actuator_handlers[actuator_type]
            async with actuator_handler.update_status_transaction(activation=True):
                actuator_handler.activate()
            self._activated_actuators.add(actuator_type)
            actuator_handler.reset_cached_actuators()
        # Mount PID controllers
        self._pids = {}
        for climate_parameter in controllable_parameters:
            pid = self.get_pid(climate_parameter)
            pid.reset()
            self.pids[climate_parameter] = pid

    async def _stop(self) -> None:
        # Deactivate activated actuator handlers
        for actuator_type in [*self.actuator_handlers.keys()]:
            if actuator_type in self._activated_actuators:
                actuator_handler = self.actuator_handlers[actuator_type]
                async with actuator_handler.update_status_transaction(activation=True):
                    actuator_handler.deactivate()
        # Reset actuator handlers and PIDs
        self._actuator_handlers = None
        self._pids = None

    def get_hardware_needed_uid(self) -> set[str]:
        hardware_needed: set[str] = set()
        expected_actuators = self.compute_expected_actuators()
        for actuator_type in expected_actuators:
            extra = set(self.ecosystem.get_hardware_group_uids(actuator_type))
            hardware_needed = hardware_needed | extra
        return hardware_needed

    async def refresh(self) -> None:
        # Refresh hardware
        await super().refresh()
        # Make sure the routine is still running
        if not self.started:
            return
        # Make sure PIDs are in sync with actuator handlers
        assert self._pids is not None
        # Activate, deactivate and reset actuator handlers if required
        currently_expected: set[gv.HardwareType] = set(self.compute_expected_actuators())
        for actuator_type, actuator_handler in self.actuator_handlers.items():
            # Reset cached actuators
            actuator_handler.reset_cached_actuators()
            # Update actuator handlers active status if required
            if not actuator_handler.active and actuator_type in currently_expected:
                async with actuator_handler.update_status_transaction(activation=True):
                    actuator_handler.activate()
                self._activated_actuators.add(actuator_type)
            elif actuator_handler.active and actuator_type not in currently_expected:
                async with actuator_handler.update_status_transaction(activation=True):
                    if actuator_handler.mode is gv.ActuatorMode.automatic:
                        await actuator_handler.reset()
                    actuator_handler.deactivate()
                self._activated_actuators.remove(actuator_type)
        # Reset PIDs
        for pid in self.pids.values():
            pid.reset()

    """Routine specific methods"""
    def get_actuator_handler(self, actuator_type: gv.HardwareType) -> ActuatorHandler:
        return self.ecosystem.actuator_hub.get_handler(actuator_type)

    @property
    def actuator_handlers(self) -> dict[gv.HardwareType, ActuatorHandler]:
        if self._actuator_handlers is None:
            raise ValueError(
                "actuator_handlers is not defined in non-started Climate subroutine")
        return self._actuator_handlers

    def get_pid(self, climate_parameter: gv.ClimateParameter) -> HystericalPID:
        return self.ecosystem.actuator_hub.get_pid(climate_parameter)

    @property
    def pids(self) -> dict[gv.ClimateParameter, HystericalPID]:
        if self._pids is None:
            raise ValueError(
                "pids is not defined in non-started Climate subroutine")
        return self._pids

    # Climate parameters and actuators management
    def compute_expected_actuators(self) -> dict[gv.HardwareType, gv.ClimateParameter]:
        regulated_parameters: list[gv.ClimateParameter] = [
            gv.ClimateParameter.temperature,
            gv.ClimateParameter.humidity,
        ]

        # Make sure the sensor subroutine is running
        if not self.ecosystem.get_subroutine_status("sensors"):
            self.logger.warning(
                "Climate subroutine requires a running sensors subroutine in "
                "order to work.")
            return {}

        # Check if climate parameters are available in the config file
        for climate_param in regulated_parameters:
            try:
                self.config.get_climate_parameter(climate_param.name)
            except UndefinedParameter:
                regulated_parameters.remove(climate_param)
        if not regulated_parameters:
            self.logger.warning("No climate parameter found.")
            return {}

        # Get sensors mounted and the measures they're taking
        sensors: Sequence[BaseSensor] = self.ecosystem.subroutines["sensors"].hardware.values()
        measures: set[str] = {
            measure.name
            for sensor in sensors
            for measure in sensor.measures
        }

        # Check if sensors taking regulated params are available
        for climate_param in regulated_parameters:
            if climate_param.name not in measures:
                regulated_parameters.remove(climate_param)
        if not regulated_parameters:
            self.logger.debug("No sensor measuring regulated parameters detected.")
            return {}

        # Check if there are regulators available and map them with climate parameters
        rv: dict[gv.HardwareType, gv.ClimateParameter] = {}
        for climate_param in regulated_parameters:
            actuator_couple: ActuatorCouple = actuator_couples[climate_param]
            for actuator_type in actuator_couple:
                if (
                        actuator_type is not None
                        and self.ecosystem.get_hardware_group_uids(actuator_type)
                ):
                    rv[actuator_type] = climate_param
        if not rv:
            self.logger.debug("No climatic actuator detected.")
            return {}
        return rv

    @property
    def regulated_parameters(self) -> list[gv.ClimateParameter]:
        if not self.started:
            return []
        expected_actuators = self.compute_expected_actuators()
        return [*set(expected_actuators.values())]

    # Routine specific methods
    def _check_misses(self) -> bool:
        if self._sensor_miss >= MISSES_BEFORE_STOP:
            self.logger.error(
                "Maximum number of Sensors data miss reached, stopping "
                "climate subroutine."
            )
            return True
        return False

    async def _get_sensors_average(self) -> dict[str, float]:
        # Get the sensors average
        prior_sensor_miss = self._sensor_miss
        sensors_subroutine: Sensors = self.ecosystem.subroutines["sensors"]
        sensors_data = sensors_subroutine.sensors_data
        sensors_average: dict[str, float]

        if isinstance(sensors_data, gv.Empty):
            self.logger.debug(
                f"No sensor data found, climate subroutine will try again "
                f"{MISSES_BEFORE_STOP - self._sensor_miss} times before "
                f"stopping.")
            self._sensor_miss += 1
            sensors_average = {}
        else:
            sensors_average = {
                data.measure: data.value
                for data in sensors_data.average
            }

        # Make sure we have sensors data for all the regulated parameters
        missing_parameter: bool = False
        for climate_parameter in self.regulated_parameters:
            if not sensors_average.get(climate_parameter, False):
                missing_parameter = True
                self.logger.debug(
                    f"No sensor data found for {climate_parameter}, climate "
                    f"subroutine will try again "
                    f"{MISSES_BEFORE_STOP - self._sensor_miss} times before "
                    f"adjusting its regulated parameters.")
        if missing_parameter:
            self._sensor_miss += 1

        # Reset sensor miss counter if there wasn't any new miss
        if self._sensor_miss == prior_sensor_miss:
            self._sensor_miss = 0

        return sensors_average

    def _update_pid(
            self,
            climate_parameter: gv.ClimateParameter,
            sensors_average: dict[str, float],
    ) -> None:
        pid: HystericalPID = self.pids[climate_parameter]
        target, hysteresis = self.compute_target(climate_parameter)
        pid.target = target
        pid.hysteresis = hysteresis
        current_value: float | None = sensors_average.get(climate_parameter)
        pid.update_pid(current_value)

    async def _update_actuator_handler(self, actuator_handler: ActuatorHandler) -> None:
        pid = actuator_handler.get_associated_pid()
        expected_status = actuator_handler.compute_expected_status(pid.last_output)
        if expected_status:
            await actuator_handler.turn_on()
            await actuator_handler.set_level(abs(pid.last_output))
        else:
            await actuator_handler.turn_off()
            await actuator_handler.set_level(0.0)

    async def _update_climate_actuators(self) -> None:
        sensors_average: dict[str, float] = await self._get_sensors_average()
        if self._check_misses():
            await self.stop()
            return
        for climate_parameter in self.regulated_parameters:
            self._update_pid(climate_parameter, sensors_average)
        for actuator_type in self.compute_expected_actuators():
            actuator_handler = self.actuator_handlers[actuator_type]
            async with actuator_handler.update_status_transaction():
                await self._update_actuator_handler(actuator_handler)

    """API calls"""
    def _compute_target_status(self, _now: time | None = None) -> bool:
        now = _now or datetime.now().time()
        hours = self.config.lighting_hours
        return hours.morning_start <= now <= hours.evening_end

    def compute_target(
            self,
            climate_parameter: gv.ClimateParameter,
            _now: time | None = None,
    ) -> tuple[float, float]:
        climate_cfg = self.config.get_climate_parameter(climate_parameter.name)
        chaos_factor = self.config.get_chaos_factor()
        now = _now or datetime.now().time()
        target_status = self._compute_target_status(now)
        if target_status:
            target = climate_cfg.day * chaos_factor
        else:
            target = climate_cfg.night * chaos_factor
        hysteresis = climate_cfg.hysteresis * chaos_factor
        return target, hysteresis

    async def turn_climate_actuator(
        self,
        climate_actuator: gv.HardwareType | str,
        turn_to: gv.ActuatorModePayload = gv.ActuatorModePayload.automatic,
        countdown: float = 0.0,
    ) -> None:
        if not self._started:
            raise RuntimeError("Climate subroutine is not started")
        climate_actuator: gv.HardwareType = gv.safe_enum_from_name(
            gv.HardwareType, climate_actuator)
        assert climate_actuator in gv.HardwareType.climate_actuator
        if self._started:
            actuator_handler: ActuatorHandler = self.ecosystem.actuator_hub.get_handler(
                climate_actuator)
            async with actuator_handler.update_status_transaction():
                await actuator_handler.turn_to(turn_to, countdown)
        else:
            raise RuntimeError(
                f"Climate subroutine is not started in ecosystem {self.ecosystem}")
