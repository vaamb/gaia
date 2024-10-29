from __future__ import annotations

from datetime import datetime, time
from time import monotonic
import typing as t

import gaia_validators as gv

from gaia.actuator_handler import ActuatorCouple, actuator_couples, HystericalPID
from gaia.exceptions import UndefinedParameter
from gaia.hardware import actuator_models
from gaia.hardware.abc import Dimmer, Hardware, Switch
from gaia.subroutines.template import SubroutineTemplate


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.actuator_handler import ActuatorHandler
    from gaia.subroutines.sensors import Sensors


MISSES_BEFORE_STOP = 5


class Climate(SubroutineTemplate):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hardware_choices = actuator_models
        self.hardware: dict[str, Dimmer | Switch]
        loop_period = float(self.ecosystem.engine.config.app_config.CLIMATE_LOOP_PERIOD)
        self._loop_period: float = max(loop_period, 10.0)
        self._sensor_miss: int = 0
        self._regulated_parameters: dict[gv.ClimateParameter: bool] = {
            gv.ClimateParameter.temperature: False,
            gv.ClimateParameter.humidity: False,
        }
        self._activated_actuator_types: set[gv.HardwareType.actuator] = set()
        self._finish__init__()

    @staticmethod
    def _any_regulated(parameters_dict: dict[gv.ClimateParameter: bool]) -> bool:
        return any([regulated for regulated in parameters_dict.values()])

    def _compute_regulated_parameters(self) -> dict[gv.ClimateParameter: bool]:
        regulated_parameters: dict[gv.ClimateParameter: bool] = {
            gv.ClimateParameter.temperature: True,
            gv.ClimateParameter.humidity: True,
        }

        # Make sure the sensor subroutine is running
        if not self.ecosystem.get_subroutine_status("sensors"):
            self.logger.warning(
                "Climate subroutine requires a running sensors subroutine in "
                "order to work."
            )
            for climate_param in regulated_parameters.keys():
                regulated_parameters[climate_param] = False
            return regulated_parameters

        # Check if target values in config
        for climate_param in regulated_parameters:
            try:
                self.config.get_climate_parameter(climate_param.name)
            except UndefinedParameter:
                regulated_parameters[climate_param] = False
        if not self._any_regulated(regulated_parameters):
            self.logger.debug("No climate parameter found.")
            return regulated_parameters

        # Get sensors mounted and the measures they're taking
        sensors = [
            hardware
            for hardware in Hardware.get_mounted().values()
            if hardware.ecosystem_uid == self.ecosystem.uid
            and hardware.type == gv.HardwareType.sensor
        ]
        measures: set[str] = set()
        for sensor in sensors:
            measures.update([measure.name for measure in sensor.measures])

        # Check if sensors taking regulated params are available
        for climate_param, regulated in regulated_parameters.items():
            if not regulated:
                continue
            if climate_param.name not in measures:
                regulated_parameters[climate_param] = False
        if not self._any_regulated(regulated_parameters):
            self.logger.debug("No sensor measuring regulated parameters detected.")
            return regulated_parameters

        # Check if regulators available
        for climate_param, regulated in regulated_parameters.items():
            if not regulated:
                continue
            actuator_couple: ActuatorCouple = actuator_couples[climate_param]
            any_regulator = False
            for actuator_type in actuator_couple:
                if actuator_type is None:
                    continue
                if self.config.get_IO_group_uids(actuator_type):
                    any_regulator = True
                    break
            if not any_regulator:
                regulated_parameters[climate_param] = False
        if not self._any_regulated(regulated_parameters):
            self.logger.debug("No climatic actuator detected.")
            return regulated_parameters
        return regulated_parameters

    async def _update_climate_actuators(self) -> None:
        sensors_subroutine: Sensors = self.ecosystem.subroutines["sensors"]
        sensors_data = sensors_subroutine.sensors_data
        if isinstance(sensors_data, gv.Empty):
            self.logger.debug(
                f"No sensor data found, climate subroutine will try again "
                f"{MISSES_BEFORE_STOP - self._sensor_miss} times before "
                f"stopping."
            )
            self._sensor_miss += 1
            self._check_misses()
            return

        self._sensor_miss = 0
        sensors_average: dict[str, float] = {
            data.measure: data.value for data in sensors_data.average
        }
        for climate_parameter in self.regulated_parameters:
            # Minimal change between run, should be ok to change pid target
            pid: HystericalPID = self.ecosystem.actuator_hub.get_pid(climate_parameter)
            target, hysteresis = self.compute_target(climate_parameter)
            pid.target = target
            pid.hysteresis = hysteresis

            # Current value is None if there is no sensor reading for it
            current_value: float | None = sensors_average.get(climate_parameter, None)
            if current_value is None:
                pid_output = 0.0  # TODO: log and add a miss ?
            else:
                pid_output = pid.update_pid(current_value)

            actuator_couple: ActuatorCouple = actuator_couples[climate_parameter]
            for direction_name, actuator_type in actuator_couple.items():
                actuator_handler = self.ecosystem.actuator_hub.get_handler(
                    actuator_type)
                if not actuator_handler.get_linked_actuators():
                    # No actuator to act on, go next
                    continue
                async with actuator_handler.update_status_transaction():
                    expected_status = actuator_handler.compute_expected_status(
                        pid_output)
                    if expected_status:
                        await actuator_handler.turn_on()
                        await actuator_handler.set_level(abs(pid_output))
                    else:
                        await actuator_handler.turn_off()
                        await actuator_handler.set_level(0.0)

    def _check_misses(self) -> None:
        if self._sensor_miss >= MISSES_BEFORE_STOP:
            self.logger.error(
                "Maximum number of Sensors data miss reached, stopping "
                "climate subroutine."
            )
            self.stop()

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
        self.update_regulated_parameters()
        if not self._any_regulated(self._regulated_parameters):
            self.logger.warning(
                "No parameters that could be regulated were found. "
                "Disabling Climate subroutine."
            )
            return False
        else:
            return True

    async def _start(self) -> None:
        # self.update_regulated_parameters()  # Done in _compute_if_manageable
        self.logger.info(
            f"Starting the climate loop. It will run every "
            f"{self._loop_period:.1f} s.")
        for climate_parameter in self._regulated_parameters:
            pid = self.ecosystem.actuator_hub.get_pid(climate_parameter)
            pid.reset()
        #self.ecosystem.engine.scheduler.add_job(
        #    func=self.routine,
        #    id=f"{self.ecosystem.uid}-climate_routine",
        #    trigger=IntervalTrigger(seconds=self._loop_period, jitter=self._loop_period/10),
        #)
        activated_actuator_types: set[gv.HardwareType] = set()
        for parameter in self._regulated_parameters:
            actuator_couple: ActuatorCouple = actuator_couples[parameter]
            for actuator_type in actuator_couple:
                # Check if we have at least one actuator available
                if not self.config.get_IO_group_uids(actuator_type):
                    continue
                actuator_handler = self.ecosystem.actuator_hub.get_handler(
                    actuator_type)
                async with actuator_handler.update_status_transaction(activation=True):
                    actuator_handler.activate()
                activated_actuator_types.add(actuator_type)
        self._activated_actuator_types = activated_actuator_types

    async def _stop(self) -> None:
        #self.ecosystem.engine.scheduler.remove_job(
        #    f"{self.ecosystem.uid}-climate_routine")
        for actuator_type in self._activated_actuator_types:
            actuator_handler = self.ecosystem.actuator_hub.get_handler(actuator_type)
            async with actuator_handler.update_status_transaction(activation=True):
                actuator_handler.deactivate()
        self._activated_actuator_types = set()

    """API calls"""
    def get_hardware_needed_uid(self) -> set[str]:
        self.update_regulated_parameters()
        hardware_needed: set[str] = set()
        for climate_parameter in self._regulated_parameters:
            couple = actuator_couples[climate_parameter]
            for IO_type in couple:
                extra = set(self.config.get_IO_group_uids(IO_type))
                hardware_needed = hardware_needed | extra
        return hardware_needed

    async def refresh_hardware(self) -> None:
        await super().refresh_hardware()
        for actuator_type in gv.HardwareType.climate_actuator:
            actuator_handler = self.ecosystem.actuator_hub.get_handler(actuator_type)
            actuator_handler.reset_cached_actuators()

    @property
    def regulated_parameters(self) -> list[gv.ClimateParameter]:
        if not self.started:
            return []
        return [
            climate_param
            for climate_param, regulated in self._regulated_parameters.items()
            if regulated
        ]

    def update_regulated_parameters(self) -> None:
        self._regulated_parameters = self._compute_regulated_parameters()

    def compute_target(
            self,
            climate_parameter: gv.ClimateParameter,
            _now: time | None = None,
    ) -> tuple[float, float]:
        parameter = self.config.get_climate_parameter(climate_parameter.name)
        now: time = _now or datetime.now().astimezone().time()
        chaos_factor = self.config.get_chaos_factor()
        lighting_hours = self.config.lighting_hours
        if lighting_hours.morning_start < now <= lighting_hours.evening_end:
            target = parameter.day * chaos_factor
        else:
            target = parameter.night * chaos_factor
        hysteresis = parameter.hysteresis * chaos_factor
        return target, hysteresis

    async def turn_climate_actuator(
        self,
        climate_actuator: gv.HardwareType.climate_actuator | str,
        turn_to: gv.ActuatorModePayload = gv.ActuatorModePayload.automatic,
        countdown: float = 0.0,
    ) -> None:
        climate_actuator: gv.HardwareType = gv.safe_enum_from_name(
            gv.HardwareType, climate_actuator)
        assert climate_actuator in gv.HardwareType.climate_actuator
        if self._started:
            actuator_handler: ActuatorHandler = self.ecosystem.actuator_hub.get_handler(
                climate_actuator)
            await actuator_handler.turn_to(turn_to, countdown)
        else:
            raise RuntimeError(
                f"Climate subroutine is not started in ecosystem {self.ecosystem}")
