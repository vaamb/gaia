from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import enum
import typing as t
from typing import cast, Literal, TypedDict

from simple_pid import PID

import gaia_validators as gv

from gaia.exceptions import UndefinedParameter
from gaia.hardware import actuator_models
from gaia.shared_resources import get_scheduler
from gaia.subroutines.template import SubroutineTemplate


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.actuator_handler import ActuatorHandler
    from gaia.subroutines.sensors import Sensors


ClimateParameterNames = Literal["temperature", "humidity"]
ClimateActuatorNames = Literal["heater", "cooler", "humidifier", "dehumidifier"]

class CoupleDirection(enum.Enum):
    increase = enum.auto()
    decrease = enum.auto()


@dataclass(frozen=True)
class ActuatorCouple:
    increase: str
    decrease: str

    def __iter__(self):
        return iter((self.increase, self.decrease))


class ActuatorCouples(TypedDict):
    temperature: ActuatorCouple
    humidity: ActuatorCouple


actuator_couples: ActuatorCouples = {
    "temperature": ActuatorCouple("heater", "cooler"),
    "humidity": ActuatorCouple("humidifier", "dehumidifier"),
}


class ClimateTarget(TypedDict):
    day: float | None
    night: float | None
    hysteresis: float | None


class ClimateParameter(ClimateTarget):
    regulated: bool


class ClimateParameters(TypedDict):
    temperature: ClimateParameter
    humidity: ClimateParameter


def _climate_param_template() -> ClimateParameter:
    return {
        "regulated": False,
        "day": None,
        "night": None,
        "hysteresis": None
    }


def climate_parameters_template() -> ClimateParameters:
    return {
        "temperature": _climate_param_template(),
        "humidity": _climate_param_template(),
    }


class ClimatePIDs(TypedDict):
    temperature: PID
    humidity: PID


MISSES_BEFORE_STOP = 5
PID_THRESHOLD = 5


class Climate(SubroutineTemplate):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hardware_choices = actuator_models
        self._sensor_miss: int = 0
        self._parameters: ClimateParameters = climate_parameters_template()
        self._Kp = 5.0  # TODO: expose via config
        self._Ki = 0.5
        self._Kd = 1.0
        self._pids: ClimatePIDs = self._setup_pids()
        self._finish__init__()

    @staticmethod
    def _compute_target(
            target_values: ClimateParameter,
            lighting_hours: gv.LightingHours,
            chaos_factor: float = 1.0,
    ) -> tuple[float, float] | tuple[None, None]:
        now = datetime.now().astimezone().time()
        tod: Literal["day", "night"]
        if lighting_hours.morning_start < now <= lighting_hours.evening_end:
            tod = "day"
        else:
            tod = "night"
        base_target = target_values[tod]
        # Should not happen
        if base_target is None:
            return None, None
        base_target = cast(float, base_target)
        target = base_target * chaos_factor
        hysteresis = target_values["hysteresis"]
        # Should not happen
        if hysteresis is None:
            hysteresis = 0.0
        return target, hysteresis

    @staticmethod
    def expected_status(
            *,
            current_value: float | None,
            target_value: float,
            hysteresis: float | None,
            couple_direction: CoupleDirection,
    ) -> bool:
        # Fallback if automatic and missing value
        if not all((current_value, target_value)):
            return False
        if hysteresis is None:
            hysteresis = 0.0
        if abs(target_value - current_value) <= hysteresis:
            return False
        else:
            if current_value < target_value:
                # We need to increase the value
                if couple_direction is CoupleDirection.increase:
                    return True
                return False
            else:
                # We need to decrease the value
                if couple_direction is CoupleDirection.increase:
                    return False
                return True

    @property
    def lighting_hours(self) -> gv.LightData:
        return self.ecosystem.light_info

    @property
    def Kp(self) -> float:
        return self._Kp

    @property
    def Ki(self) -> float:
        return self._Ki

    @property
    def Kd(self) -> float:
        return self._Kd

    def _setup_pids(self) -> ClimatePIDs:
        return {
            climate_parameter: PID(
                self.Kp, self.Ki, self.Kd, output_limits=(-100, 100))
            for climate_parameter in ["temperature", "humidity"]
        }

    @property
    def regulated_parameters(self) -> list[ClimateParameterNames]:
        return [
            parameter for parameter in self._parameters
            if self._parameters[parameter]["regulated"]
        ] if self.started else []

    def _any_regulated(
            self,
            parameters: ClimateParameters | None = None
    ) -> bool:
        parameters = parameters or self._parameters
        return any([parameter["regulated"] for parameter in parameters.values()])

    def _compute_parameters(self) -> ClimateParameters:
        parameters = climate_parameters_template()

        # Set regulated to True by default
        for climate_param in parameters.keys():
            climate_param = cast(ClimateParameterNames, climate_param)
            parameters[climate_param]["regulated"] = True

        # Make sure the sensor subroutine is running
        if not self.ecosystem.get_subroutine_status("sensors"):
            self.logger.debug("No climate parameter found.")
            for climate_param in parameters.keys():
                climate_param = cast(ClimateParameterNames, climate_param)
                parameters[climate_param]["regulated"] = False
            return parameters

        # Check if target values in config
        for climate_param in parameters.keys():
            climate_param = cast(ClimateParameterNames, climate_param)
            try:
                param_cfg = self.config.get_climate_parameter(climate_param)
            except UndefinedParameter:
                parameters[climate_param]["regulated"] = False
            else:
                parameters[climate_param]["day"] = param_cfg.day
                parameters[climate_param]["night"] = param_cfg.night
                parameters[climate_param]["hysteresis"] = param_cfg.hysteresis
        if not self._any_regulated(parameters):
            self.logger.debug("No climate parameter found.")
            return parameters

        # Check if sensors taking regulated params are available
        measures: set[str] = set()
        if self.config.get_management("sensors"):
            for hardware_uid in self.config.get_IO_group_uids("sensor"):
                hardware = self.config.get_hardware_config(hardware_uid)
                measures.update(hardware.measures)
        else:
            self.logger.warning(
                "Climate subroutine requires a running sensors subroutine in "
                "order to work"
            )
        for climate_param, value in parameters.items():
            climate_param = cast(ClimateParameterNames, climate_param)
            if not value["regulated"]:
                continue
            if climate_param not in measures:
                parameters[climate_param]["regulated"] = False
        if not self._any_regulated(parameters):
            self.logger.debug(
                "No sensor measuring regulated parameters detected.")
            return parameters

        # Check if regulators available
        for climate_param, value in parameters.items():
            climate_param = cast(ClimateParameterNames, climate_param)
            if not value:
                continue
            regulator_couple: ActuatorCouple = actuator_couples[climate_param]
            any_regulator = False
            for direction in regulator_couple:
                direction: ClimateActuatorNames
                if self.config.get_IO_group_uids(direction):
                    any_regulator = True
            if not any_regulator:
                parameters[climate_param]["regulated"] = False
        if not self._any_regulated(parameters):
            self.logger.debug(
                "No climatic actuator detected.")
            return parameters
        return parameters

    def _compute_if_manageable(self) -> bool:
        self._parameters = self._compute_parameters()
        if not self._any_regulated(self._parameters):
            self.logger.warning(
                "No parameters that could be regulated were found. "
                "Disabling Climate subroutine."
            )
            return False
        else:
            return True

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

        sensors_subroutine: "Sensors" = self.ecosystem.subroutines["sensors"]
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

        sensors_average = {
            data.measure: data.value for data in sensors_data.average
        }
        for parameter in self.regulated_parameters:
            # TODO: migrate `_compute_target` inside `expected_status`
            target_value, hysteresis = self._compute_target(
                self._parameters[parameter], self.lighting_hours,
                self.ecosystem.config.chaos_factor)
            if target_value is None:
                raise RuntimeError(
                    f"Error while computing the target for parameter '{parameter}'"
                )

            actuator_couple: ActuatorCouple = actuator_couples[parameter]
            # Minimal change between run, should be ok to change the setpoint
            self._pids[parameter].setpoint = target_value
            # Current value is None if there is no sensor reading for it
            current_value: float | None = sensors_average.get(parameter, None)
            if current_value:
                pid_output = self._pids[parameter](current_value)
            else:
                pid_output = None
                self._pids[parameter].reset()

            for couple_direction in CoupleDirection:
                actuator_name: ClimateActuatorNames = getattr(
                    actuator_couple, couple_direction.name)
                actuator_handler = self.get_actuator_handler(actuator_name)
                expected_status = actuator_handler.compute_expected_status(
                    current_value=current_value, target_value=target_value,
                    hysteresis=hysteresis, couple_direction=couple_direction)
                if expected_status:
                    actuator_handler.turn_on()
                    if pid_output is not None:
                        actuator_handler.set_level(pid_output)
                else:
                    actuator_handler.turn_off()

    def _check_misses(self) -> None:
        if self._sensor_miss >= MISSES_BEFORE_STOP:
            self.logger.error(
                "Maximum number of Sensors data miss reached, stopping "
                "climate subroutine."
            )
            self.stop()

    def _start(self) -> None:
        self._parameters = self._compute_parameters()
        self.logger.info(
            f"Starting climate routine. It will run every minute"
        )
        for pid in self._pids.values():
            pid.reset()
        scheduler = get_scheduler()
        scheduler.add_job(
            self._climate_routine,
            trigger="cron", minute="*", misfire_grace_time=10,
            id=f"{self.ecosystem.name}-climate"
        )
        for parameter in self.regulated_parameters:
            actuator_couple: ActuatorCouple = actuator_couples[parameter]
            for couple_direction in CoupleDirection:
                actuator_name: ClimateActuatorNames = getattr(
                    actuator_couple, couple_direction.name)
                actuator_handler = self.get_actuator_handler(actuator_name)
                actuator_handler.activate()

    def _stop(self) -> None:
        scheduler = get_scheduler()
        scheduler.remove_job(job_id=f"{self.ecosystem.name}-climate")
        for parameter in self.regulated_parameters:
            actuator_couple: ActuatorCouple = actuator_couples[parameter]
            for couple_direction in CoupleDirection:
                actuator_name: ClimateActuatorNames = getattr(
                    actuator_couple, couple_direction.name)
                actuator_handler = self.get_actuator_handler(actuator_name)
                actuator_handler.deactivate()

    """API calls"""
    def get_actuator_handler(
            self,
            actuator_type: gv.HardwareType | ClimateActuatorNames
    ) -> ActuatorHandler:
        verified_actuator_type = gv.safe_enum_from_name(gv.HardwareType, actuator_type)
        return self.ecosystem.actuator_handlers.get_handler(verified_actuator_type)

    def get_hardware_needed_uid(self) -> set[str]:
        self.update_climate_parameters()
        hardware_needed: set[str] = set()
        for couple in actuator_couples.values():
            for IO_type in couple:
                extra = set(self.config.get_IO_group_uids(IO_type))
                hardware_needed = hardware_needed | extra
        return hardware_needed

    def update_climate_parameters(self) -> None:
        self._parameters = self._compute_parameters()

    @property
    def regulated(self) -> set[ClimateParameterNames]:
        return {
            climate_parameter for climate_parameter, value
            in self._parameters.items()
            if value["regulated"]
        }

    @property
    def targets(self) -> dict[ClimateParameterNames, ClimateTarget]:
        return {
            parameter: {
                "day": value["day"],
                "night": value["night"],
                "hysteresis": value["hysteresis"],
            } for parameter, value in self._parameters.items()
        }

    def turn_climate_actuator(
            self,
            climate_actuator: gv.HardwareType | str,
            turn_to: gv.ActuatorModePayload = gv.ActuatorModePayload.automatic,
            countdown: float = 0.0
    ) -> None:
        climate_actuator: gv.HardwareType = gv.safe_enum_from_name(
            gv.HardwareType, climate_actuator)
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
