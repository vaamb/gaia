from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import typing as t
from typing import cast, Literal, TypedDict

from simple_pid import PID

from gaia_validators import (
    ActuatorModePayload, Empty, HardwareConfig, HardwareType, LightData,
    LightingHours, safe_enum_from_name)

from gaia.exceptions import UndefinedParameter
from gaia.hardware import actuator_models
from gaia.hardware.abc import Dimmer, Hardware, Switch
from gaia.shared_resources import get_scheduler
from gaia.actuator_handler import ActuatorHandler
from gaia.subroutines.template import SubroutineTemplate


if t.TYPE_CHECKING:  # pragma: no cover
    pass


ClimateParameterNames = Literal["temperature", "humidity"]
ClimateActuatorNames = Literal["heater", "cooler", "humidifier", "dehumidifier"]


@dataclass(frozen=True)
class ActuatorCouple:
    increase: str
    decrease: str

    def __iter__(self):
        return iter((self.increase, self.decrease))


class ActuatorCouples(TypedDict):
    temperature: ActuatorCouple
    humidity: ActuatorCouple


REGULATORS: ActuatorCouples = ActuatorCouples(**{
    "temperature": ActuatorCouple("heater", "cooler"),
    "humidity": ActuatorCouple("humidifier", "dehumidifier"),
})


class ClimateActuators(TypedDict):
    heater: ActuatorHandler
    cooler: ActuatorHandler
    humidifier: ActuatorHandler
    dehumidifier: ActuatorHandler


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
        "regulated": True,
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
        self._sensor_miss: int = 0
        self.actuators: ClimateActuators = self._setup_actuators()
        self._parameters: ClimateParameters = climate_parameters_template()
        self._Kp = 5.0  # TODO: expose via config
        self._Ki = 0.5
        self._Kd = 1.0
        self._pids: ClimatePIDs = self._setup_pids()
        self._finish__init__()

    @staticmethod
    def _compute_target(
            target_values: ClimateParameter,
            lighting_hours: LightingHours,
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
            current_value: float,
            target_value: float,
            hysteresis: float
    ) -> bool:
        if abs(target_value - current_value) < hysteresis:
            return False
        else:
            return True

    @property
    def lighting_hours(self) -> LightData:
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

    def _setup_actuators(self) -> ClimateActuators:
        return {
            actuator_name: ActuatorHandler(
                self,
                safe_enum_from_name(HardwareType, actuator_name),
                self.expected_status
            )
            for actuator_name in ["heater", "cooler", "humidifier", "dehumidifier"]
        }

    def _setup_pids(self) -> ClimatePIDs:
        return {
            climate_parameter: PID(
                self.Kp, self.Ki, self.Kd, output_limits=(-100, 100))
            for climate_parameter in ["temperature", "humidity"]
        }

    def _any_regulated(
            self,
            parameters: ClimateParameters | None = None
    ) -> bool:
        parameters = parameters or self._parameters
        return any([parameter["regulated"] for parameter in parameters.values()])

    def _compute_parameters(self) -> ClimateParameters:
        parameters = climate_parameters_template()
        for actuator_handler in self.actuators.values():
            actuator_handler.active = False

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
            self.logger.debug(
                "No climate parameter found.")
            return parameters

        # Check if sensors taking regulated params are available
        measures: set[str] = set()
        if self.config.get_management("sensors"):
            for hardware_uid in self.config.get_IO_group_uids("sensor"):
                hardware = self.config.get_hardware_config(hardware_uid)
                measures.update(hardware.measures)
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
            regulator_couple: ActuatorCouple = REGULATORS[climate_param]
            any_regulator = False
            for direction in regulator_couple:
                direction: ClimateActuatorNames
                if self.config.get_IO_group_uids(direction):
                    self.actuators[direction].active = True
                    any_regulator = True
            if not any_regulator:
                parameters[climate_param]["regulated"] = False
        if not self._any_regulated(parameters):
            self.logger.debug(
                "No climatic actuator detected.")
            return parameters
        return parameters

    def _update_manageable(self) -> None:
        self._parameters = self._compute_parameters()
        if not self._any_regulated():
            self.logger.warning(
                "No parameters that could be regulated were found. "
                "Disabling Climate subroutine."
            )
            self.manageable = False
        else:
            self.manageable = True

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

        sensors_subroutine = cast("Sensors", self.ecosystem.subroutines["sensors"])
        sensors_data = sensors_subroutine.sensors_data
        if isinstance(sensors_data, Empty):
            self.logger.debug(
                f"No sensor data found, climate subroutine will try again "
                f"{MISSES_BEFORE_STOP - self._sensor_miss} times before "
                f"stopping."
            )
            self._sensor_miss += 1
            self._check_misses()
            return

        def activate(
                actuator_handler: ActuatorHandler,
                pid_output: float
        ) -> None:
            actuator_handler.status = True
            actuator_list = cast(
                list[Switch],
                Hardware.get_actives_by_type(actuator_handler.type.value).values())
            for actuator in actuator_list:
                actuator.turn_on()
                if isinstance(actuator, Dimmer):
                    actuator.set_pwm_level(pid_output)

        def deactivate(actuator_handler: ActuatorHandler) -> None:
            actuator_handler.status = False
            actuator_list = cast(
                list[Switch],
                Hardware.get_actives_by_type(actuator_handler.type.value).values())
            for actuator in actuator_list:
                actuator.turn_off()

        average = sensors_data.average
        for data in average:
            climate_param = data.measure
            if not self._parameters.get(climate_param, {}).get("regulated"):
                continue
            climate_param = cast(ClimateParameterNames, climate_param)
            current_value = data.value
            target_value, hysteresis = self._compute_target(
                self._parameters[climate_param], self.lighting_hours,
                self.ecosystem.chaos.factor)
            if target_value is None:
                continue
            actuator_couple: ActuatorCouple = REGULATORS[climate_param]
            self._pids[climate_param].setpoint = target_value
            pid_output = self._pids[climate_param](current_value)

            for actuator_direction in ["increase", "decrease"]:
                actuator_name: ClimateActuatorNames = getattr(
                    actuator_couple, actuator_direction)
                actuator_handler: ActuatorHandler = self.actuators[actuator_name]
                if not actuator_handler.active:
                    continue
                expected_status = actuator_handler.compute_expected_status(
                    current_value=current_value, target_value=target_value,
                    hysteresis=hysteresis)
                if expected_status:
                    if pid_output > PID_THRESHOLD:
                        if actuator_direction == "increase":
                            activate(actuator_handler, pid_output)
                        else:
                            deactivate(actuator_handler)
                    elif pid_output < -PID_THRESHOLD:
                        if actuator_direction == "increase":
                            deactivate(actuator_handler)
                        else:
                            activate(actuator_handler, -pid_output)

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
            id=f"{self._ecosystem_name}-climate"
        )

    def _stop(self) -> None:
        scheduler = get_scheduler()
        scheduler.remove_job(job_id=f"{self._ecosystem_name}-climate")

    """API calls"""
    def add_hardware(self, hardware_config: HardwareConfig) -> None:
        self._add_hardware(hardware_config, actuator_models)

    def remove_hardware(self, hardware_uid: str) -> None:
        try:
            del self.hardware[hardware_uid]
        except KeyError:
            self.logger.error(f"Regulator '{hardware_uid}' does not exist")

    def get_hardware_needed_uid(self) -> set[str]:
        self.update_climate_parameters()
        hardware_needed: set[str] = set()
        for couple in REGULATORS.values():
            for IO_type in couple:
                extra = set(self.config.get_IO_group_uids(IO_type))
                hardware_needed = hardware_needed | extra
        return hardware_needed

    def turn_regulator_to(
            self,
            actuator: HardwareType,
            mode: ActuatorModePayload
    ) -> None:
        pass

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
            climate_actuator: HardwareType | str,
            turn_to: ActuatorModePayload = ActuatorModePayload.automatic,
            countdown: float = 0.0
    ) -> None:
        climate_actuator: HardwareType = safe_enum_from_name(
            HardwareType, climate_actuator)
        if climate_actuator not in [
            HardwareType.heater, HardwareType.cooler, HardwareType.humidifier,
            HardwareType.dehumidifier
        ]:
            raise TypeError(
                "'climate_actuator' should be a valid climate actuator")
        if self._started:
            self.actuators[climate_actuator.value].turn_to(turn_to, countdown)
        else:
            raise RuntimeError(
                f"{self.name} is not started in engine {self.ecosystem}")
