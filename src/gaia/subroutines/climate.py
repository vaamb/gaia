from __future__ import annotations

from datetime import datetime, time
from time import monotonic
import typing as t

import gaia_validators as gv

from gaia.actuator_handler import HystericalPID
from gaia.hardware import actuator_models
from gaia.hardware.abc import Dimmer, Switch
from gaia.subroutines.template import SubroutineTemplate


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.actuator_handler import ActuatorHandler
    from gaia.subroutines.sensors import Sensors


MISSES_BEFORE_STOP = 5

REGULABLE_PARAMETERS: list[gv.ClimateParameter] = [
    gv.ClimateParameter.temperature,
    gv.ClimateParameter.humidity,
]


class Climate(SubroutineTemplate[Dimmer | Switch]):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hardware_choices = actuator_models
        # Routine parameters
        loop_period = float(self.ecosystem.engine.config.app_config.CLIMATE_LOOP_PERIOD)
        self._loop_period: float = max(loop_period, 10.0)
        self._actuator_handlers: dict[str, ActuatorHandler] | None = None
        self._pids: dict[gv.ClimateParameter, HystericalPID] | None = None
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
        # Mount required actuator handlers
        self._actuator_handlers = {}
        expected_actuators = self.compute_expected_actuators()
        for actuator_group in expected_actuators:
            actuator_handler = self.get_actuator_handler(actuator_group)
            self.actuator_handlers[actuator_group] = actuator_handler
            await self._activate_actuator_handler(actuator_group)
            actuator_handler.reset_cached_actuators()
        # Mount PID controllers
        # TODO: mount PID controllers only if required
        self._pids = {}
        for climate_parameter in REGULABLE_PARAMETERS:
            pid = self.get_pid(climate_parameter)
            pid.reset()
            self.pids[climate_parameter] = pid

    async def _stop(self) -> None:
        # Deactivate activated actuator handlers
        for actuator_group in [*self.actuator_handlers.keys()]:
            await self._deactivate_actuator_handler(actuator_group)
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
        currently_expected: set[str] = set(self.compute_expected_actuators())
        currently_mounted: set[str] = set(self.actuator_handlers.keys())
        for actuator_group in currently_expected - currently_mounted:
            actuator_handler = self.get_actuator_handler(actuator_group)
            self.actuator_handlers[actuator_group] = actuator_handler
            await self._activate_actuator_handler(actuator_group)
        for actuator_group in currently_mounted - currently_expected:
            await self._deactivate_actuator_handler(actuator_group)
            del self.actuator_handlers[actuator_group]
        # Reset actuator handlers
        for actuator_group in self.actuator_handlers:
            self.actuator_handlers[actuator_group].reset_cached_actuators()
        # Reset PIDs
        for pid in self.pids.values():
            pid.reset()

    """Routine specific methods"""
    def get_actuator_handler(self, actuator_group: str) -> ActuatorHandler:
        return self.ecosystem.actuator_hub.get_handler(actuator_group)

    @property
    def actuator_handlers(self) -> dict[str, ActuatorHandler]:
        if self._actuator_handlers is None:
            raise ValueError(
                "actuator_handlers is not defined in non-started Climate subroutine")
        return self._actuator_handlers

    async def _activate_actuator_handler(self, actuator_group: str) -> None:
        actuator_handler = self.actuator_handlers[actuator_group]
        async with actuator_handler.update_status_transaction(activation=True):
            actuator_handler.activate()

    async def _deactivate_actuator_handler(self, actuator_group: str) -> None:
        actuator_handler = self.actuator_handlers[actuator_group]
        async with actuator_handler.update_status_transaction(activation=True):
            if actuator_handler.mode is gv.ActuatorMode.automatic:
                await actuator_handler.reset()
            actuator_handler.deactivate()

    def get_pid(self, climate_parameter: gv.ClimateParameter) -> HystericalPID:
        return self.ecosystem.actuator_hub.get_pid(climate_parameter)

    @property
    def pids(self) -> dict[gv.ClimateParameter, HystericalPID]:
        if self._pids is None:
            raise ValueError(
                "pids is not defined in non-started Climate subroutine")
        return self._pids

    # Climate parameters and actuators management
    def compute_expected_actuators(self) -> dict[str, gv.ClimateParameter]:
        regulated_parameters: list[gv.ClimateParameter] = REGULABLE_PARAMETERS.copy()

        # Make sure the sensor subroutine is running
        if not self.ecosystem.get_subroutine_status("sensors"):
            self.logger.warning(
                "Climate subroutine requires a running sensors subroutine in "
                "order to work.")
            return {}

        # Check if climate parameters are available in the config file
        for climate_param in [*regulated_parameters]:
            if not self.config.has_climate_parameter(climate_param):
                regulated_parameters.remove(climate_param)
        if not regulated_parameters:
            self.logger.warning("No climate parameter found.")
            return {}

        # Get mounted sensors and the measures they're taking
        measures: set[str] = {
            measure.name
            # TODO: check if sensors are mounted.
            #  They should be if sensors subroutine is running
            for sensor in self.ecosystem.subroutines["sensors"].hardware.values()
            for measure in sensor.measures
        }

        # Check if sensors taking regulated params are available
        for climate_param in regulated_parameters:
            measure = self._get_measure_for_parameter(climate_param)
            if measure not in measures:
                regulated_parameters.remove(climate_param)
        if not regulated_parameters:
            self.logger.debug("No sensor measuring regulated parameters detected.")
            return {}

        # Check if there are regulators available and map them with climate parameters
        rv: dict[str, gv.ClimateParameter] = {}
        actuator_couples = self.config.get_actuator_couples()
        for climate_param in regulated_parameters:
            actuator_couple: gv.ActuatorCouple = actuator_couples[climate_param]
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

    def _get_measure_for_parameter(self, parameter: gv.ClimateParameter) -> str:
        climate_cfg = self.config.get_climate_parameter(parameter)
        return (
            climate_cfg.linked_measure
            if climate_cfg.linked_measure else parameter.name
        )

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
            measure = self._get_measure_for_parameter(climate_parameter)
            if not sensors_average.get(measure, False):
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
        measure = self._get_measure_for_parameter(climate_parameter)
        current_value: float | None = sensors_average.get(measure)
        pid.update_pid(current_value)

    async def _update_actuator_handler(self, actuator_handler: ActuatorHandler) -> None:
        pid = actuator_handler.associated_pid
        assert pid is not None
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
        actuator_group: str,
        turn_to: gv.ActuatorModePayload = gv.ActuatorModePayload.automatic,
        countdown: float = 0.0,
    ) -> None:
        if not self._started:
            raise RuntimeError("Climate subroutine is not started")
        if self._started:
            actuator_handler: ActuatorHandler = self.ecosystem.actuator_hub.get_handler(
                actuator_group)
            async with actuator_handler.update_status_transaction():
                await actuator_handler.turn_to(turn_to, countdown)
        else:
            raise RuntimeError(
                f"Climate subroutine is not started in ecosystem {self.ecosystem}")
