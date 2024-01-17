from __future__ import annotations

from datetime import datetime, time
import typing as t

import gaia_validators as gv

from gaia.actuator_handler import ActuatorCouple, actuator_couples, HystericalPID
from gaia.exceptions import UndefinedParameter
from gaia.hardware import actuator_models, Hardware
from gaia.shared_resources import get_scheduler
from gaia.subroutines.template import SubroutineTemplate


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.actuator_handler import ActuatorHandler
    from gaia.subroutines.sensors import Sensors


MISSES_BEFORE_STOP = 5


class Climate(SubroutineTemplate):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hardware_choices = actuator_models
        self._sensor_miss: int = 0
        self._regulated_parameters: dict[gv.ClimateParameter: bool] = {
            gv.ClimateParameter.temperature: False,
            gv.ClimateParameter.humidity: False,
        }
        self._finish__init__()

    @staticmethod
    def _any_regulated(
            parameters_dict: dict[gv.ClimateParameter: bool]
    ) -> bool:
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
                "order to work"
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
        sensors  = [
            hardware for hardware in Hardware.get_mounted().values()
            if hardware.ecosystem_uid == self.ecosystem.uid
            and hardware.type == gv.HardwareType.sensor
        ]
        measures: set[str] = set()
        for sensor in sensors:
            measures.update(sensor.measures)

        # Check if sensors taking regulated params are available
        for climate_param, regulated in regulated_parameters.items():
            if not regulated:
                continue
            if climate_param.name not in measures:
                regulated_parameters[climate_param] = False
        if not self._any_regulated(regulated_parameters):
            self.logger.debug(
                "No sensor measuring regulated parameters detected.")
            return regulated_parameters

        # Check if regulators available
        for climate_param, regulated in regulated_parameters.items():
            if not regulated:
                continue
            regulator_couple: ActuatorCouple = actuator_couples[climate_param]
            any_regulator = False
            for regulator in regulator_couple:
                if regulator is None:
                    continue
                if self.config.get_IO_group_uids(regulator.name):
                    any_regulator = True
                    break
            if not any_regulator:
                regulated_parameters[climate_param] = False
        if not self._any_regulated(regulated_parameters):
            self.logger.debug("No climatic actuator detected.")
            return regulated_parameters
        return regulated_parameters

    def _climate_routine(self) -> None:
        if not self.ecosystem.get_subroutine_status("sensors"):
            if not self.config.get_management("sensors"):
                self.logger.error(
                    "The climate subroutine requires sensors management in order to "
                    "work. Stopping the climate subroutine."
                )
                self.stop()
                return
            else:
                self.logger.debug(
                    f"Could not reach Sensors subroutine, climate subroutine will "
                    f"try again {5 - self._sensor_miss} times before stopping."
                )
                self._sensor_miss += 1
                self._check_misses()
                return

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

        sensors_average: dict[str, float] = {
            data.measure: data.value for data in sensors_data.average
        }
        for parameter in self.regulated_parameters:
            # Minimal change between run, should be ok to change pid target
            pid: HystericalPID = self.get_pid(parameter)
            target, hysteresis = self.compute_target(parameter)
            pid.target = target
            pid.hysteresis = hysteresis

            # Current value is None if there is no sensor reading for it
            current_value: float | None = sensors_average.get(parameter, None)
            if current_value is None:
                pid_output = 0.0  # TODO: log and add a miss ?
            else:
                pid_output = pid.update_pid(current_value)

            actuator_couple: ActuatorCouple = actuator_couples[parameter]
            for couple_direction in actuator_couple.directions():
                actuator_type = actuator_couple[couple_direction]
                actuator_handler = self.get_actuator_handler(actuator_type)
                if couple_direction == "increase":
                    corrected_output = pid_output
                else:
                    corrected_output = -pid_output
                expected_status = actuator_handler.compute_expected_status(
                    corrected_output)
                if expected_status:
                    actuator_handler.turn_on()
                    actuator_handler.set_level(corrected_output)
                else:
                    actuator_handler.turn_off()
                    actuator_handler.set_level(0.0)

    def _check_misses(self) -> None:
        if self._sensor_miss >= MISSES_BEFORE_STOP:
            self.logger.error(
                "Maximum number of Sensors data miss reached, stopping "
                "climate subroutine."
            )
            self.stop()

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

    def _start(self) -> None:
        # self.update_regulated_parameters()  # Done in _compute_if_manageable
        self.logger.info(
            f"Starting climate routine. It will run every minute"
        )
        for climate_parameter in self._regulated_parameters:
            pid = self.get_pid(climate_parameter)
            pid.reset()
        scheduler = get_scheduler()
        scheduler.add_job(
            self._climate_routine,
            trigger="cron", minute="*", misfire_grace_time=10,
            id=f"{self.ecosystem.name}-climate"
        )
        for parameter in self.regulated_parameters:
            actuator_couple: ActuatorCouple = actuator_couples[parameter]
            for actuator_type in actuator_couple:
                actuator_handler = self.get_actuator_handler(actuator_type)
                actuator_handler.activate()

    def _stop(self) -> None:
        scheduler = get_scheduler()
        scheduler.remove_job(job_id=f"{self.ecosystem.name}-climate")
        for parameter in self.regulated_parameters:
            actuator_couple: ActuatorCouple = actuator_couples[parameter]
            for actuator_type in actuator_couple:
                actuator_handler = self.get_actuator_handler(actuator_type)
                actuator_handler.deactivate()

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

    def get_actuator_handler(
            self,
            climate_actuator: gv.HardwareType | gv.HardwareTypeNames
    ) -> ActuatorHandler:
        climate_actuator = gv.safe_enum_from_name(gv.HardwareType, climate_actuator)
        # TODO: assert actuator type is a climatic one
        return self.ecosystem.actuator_handlers.get_handler(climate_actuator)

    def get_pid(
            self,
            climate_parameter: gv.ClimateParameter | gv.ClimateParameterNames
    ) -> HystericalPID:
        climate_parameter = gv.safe_enum_from_name(gv.ClimateParameter, climate_parameter)
        return self.ecosystem.actuator_handlers.get_pid(climate_parameter)

    @property
    def lighting_hours(self) -> gv.LightData:
        return self.ecosystem.light_info

    @property
    def regulated_parameters(self) -> list[gv.ClimateParameter]:
        return [
            climate_param for climate_param, regulated
            in self._regulated_parameters.items()
            if regulated
        ] if self.started else []

    def update_regulated_parameters(self) -> None:
        self._regulated_parameters = self._compute_regulated_parameters()

    def compute_target(
            self,
            climate_parameter: gv.ClimateParameter,
            _now: time | None = None
    ) -> tuple[float, float | None]:
        parameter = self.config.get_climate_parameter(climate_parameter.name)
        now: time = _now or datetime.now().astimezone().time()
        if self.lighting_hours.morning_start < now <= self.lighting_hours.evening_end:
            target = parameter.day * self.ecosystem.config.chaos_factor
        else:
            target = parameter.night * self.ecosystem.config.chaos_factor
        hysteresis = parameter.hysteresis * self.ecosystem.config.chaos_factor
        if hysteresis == 0.0:
            hysteresis = None
        return target, hysteresis

    def turn_climate_actuator(
            self,
            climate_actuator: gv.HardwareType | str,
            turn_to: gv.ActuatorModePayload = gv.ActuatorModePayload.automatic,
            countdown: float = 0.0
    ) -> None:
        climate_actuator: gv.HardwareType = gv.safe_enum_from_name(
            gv.HardwareType, climate_actuator)
        # TODO: assert actuator type is a climatic one
        if climate_actuator not in [
            gv.HardwareType.heater, gv.HardwareType.cooler,
            gv.HardwareType.humidifier, gv.HardwareType.dehumidifier
        ]:
            raise TypeError(
                "'climate_actuator' should be a valid climate actuator")
        if self._started:
            actuator_handler: ActuatorHandler = \
                self.ecosystem.actuator_handlers.get_handler(climate_actuator)
            actuator_handler.turn_to(turn_to, countdown)
        else:
            raise RuntimeError(
                f"Climate subroutine is not started in ecosystem {self.ecosystem}")
