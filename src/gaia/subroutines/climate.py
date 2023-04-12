from dataclasses import asdict, dataclass
from datetime import datetime
from threading import Event
import typing as t
from typing import cast

from simple_pid import PID

from gaia_validators import (
    ClimateParameterNames, Empty, HardwareConfig, LightingHours, SensorsData
)

from gaia.exceptions import StoppingSubroutine, UndefinedParameter
from gaia.hardware import actuator_models
from gaia.hardware.abc import Dimmer, Switch
from gaia.shared_resources import scheduler
from gaia.subroutines.template import SubroutineTemplate


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.subroutines.light import Light
    from gaia.subroutines.sensors import Sensors


@dataclass(frozen=True)
class RegulatorCouple:
    increase: str
    decrease: str

    def __iter__(self):
        return iter((self.increase, self.decrease))


REGULATORS = {
    "temperature": RegulatorCouple("heater", "cooler"),
    "humidity": RegulatorCouple("humidifier", "dehumidifier"),
}

MISSES_BEFORE_STOP = 5

Kp = 15
Ki = 0.5
Kd = 1


class Climate(SubroutineTemplate):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._stop_event: Event = Event()
        self._sensor_miss: int = 0
        self._pids: dict[str, PID] = {}
        self._reset_PIDs()
        self._regulators: dict[str, set] = {}
        self._reset_regulators_dict()
        self._regulated: set[ClimateParameterNames] = set()
        self._targets: dict[str, dict[str, float]] = {}
        self._sun_times: LightingHours = LightingHours()
        self._finish__init__()

    def _reset_regulators_dict(self) -> None:
        self._regulators = {
            "heater": set(),
            "cooler": set(),
            "humidifier": set(),
            "dehumidifier": set(),
        }

    def _reset_PIDs(self) -> None:
        for climate_param in REGULATORS:
            self._pids[climate_param] = PID(Kp, Ki, Kd, output_limits=(-100, 100))

    def _update_regulated(self) -> None:
        regulated: set[ClimateParameterNames] = set()
        # Check if target values in config
        for climate_param in ("temperature", "humidity", "wind"):
            try:
                self.config.get_climate_parameters(climate_param)
            except UndefinedParameter:
                pass
            else:
                regulated.add(climate_param)
        if not regulated:
            self._regulated = set()
            self.logger.debug(
                "No climate parameter found."
            )
            return

        # Check if regulators available
        regulators: list[str] = []
        for climate_param in REGULATORS:
            if climate_param in regulated:
                for regulator in REGULATORS[climate_param]:
                    if self.config.get_IO_group_uids(regulator):
                        regulators.append(regulator)
        if not regulators:
            self._regulated = set()
            self.logger.debug(
                "No climate hardware detected."
            )
            return
        for climate_param in regulated.copy():
            if not any([r in REGULATORS[climate_param] for r in regulators]):
                regulated.remove(climate_param)

        # Check if Sensors taking regulated params are available
        measures: set[str] = set()
        sensor_uid = self.config.get_IO_group_uids("sensor")
        for uid in sensor_uid:
            hardware_config = self.config.get_hardware_config(uid)
            m = hardware_config.measures
            if isinstance(m, str):
                measures.add(m)
            else:
                measures.update(m)
        for reg in REGULATORS:
            if any(
                    r in regulators for r in REGULATORS[reg]
            ):
                if reg not in measures:
                    regulated.remove(reg)
        self._regulated = regulated
        if not regulated:
            self.logger.debug(
                "Did not find any sensor measuring regulated parameters."
            )

    def _update_manageable(self) -> None:
        self._update_regulated()
        if not self._regulated:
            self.logger.warning(
                "No parameters that could be regulated were found. "
                "Disabling Climate subroutine."
            )
            self.manageable = False
        else:
            self.manageable = True

    def _update_time_parameters(self):
        if self.ecosystem.get_subroutine_status("light"):
            light_subroutine: "Light" = self.ecosystem.subroutines["light"]
            try:
                self._sun_times = LightingHours(
                    morning_start=light_subroutine.lighting_hours.morning_start,
                    evening_end=light_subroutine.lighting_hours.evening_end,
                )
            except AttributeError:
                self.logger.error(
                    "Could not obtain time parameters from the Light subroutine, "
                    "using the config ones instead."
                )
        else:
            try:
                self._sun_times = LightingHours(
                    morning_start=self.config.time_parameters.day,
                    evening_end=self.config.time_parameters.night,
                )
            except UndefinedParameter:
                self.logger.error(
                    f"No day and night parameters set for ecosystem "
                    f"{self._ecosystem_name}. Stopping the climate subroutine."
                )
                raise StoppingSubroutine

    def _climate_routine(self) -> None:
        if self.ecosystem.get_subroutine_status("sensors"):
            sensors_subroutine: "Sensors" = self.ecosystem.subroutines["sensors"]
            sensors_data = sensors_subroutine.sensors_data
            if isinstance(sensors_data, Empty):
                self.logger.debug(
                    f"No sensor data found, climate subroutine will try "
                    f"again {5 - self._sensor_miss} times before stopping."
                )
                self._sensor_miss += 1
            else:
                average = sensors_data.average
                for data in average:
                    measure = data.measure
                    if measure in self._regulated:
                        value = data.value
                        now = datetime.now().astimezone().time()
                        if self._sun_times.morning_start < now <= self._sun_times.evening_end:
                            tod = "day"
                        else:
                            tod = "night"
                        target = self._targets[measure][tod] * self.ecosystem.chaos.factor
                        hysteresis = self._targets[measure].get("hysteresis", 0)
                        self._pids[measure].setpoint = target
                        if abs(target - value) < hysteresis:
                            self._pids[
                                measure].reset()  # Keep reset or not? Risk of integral windup if not
                            output = 0
                        else:
                            output = self._pids[measure](value)
                        if output > 0:
                            regulator_increase = REGULATORS[measure].increase
                            for hardware_uid in self._regulators[regulator_increase]:
                                hardware = cast(
                                    Switch, self.hardware[hardware_uid])
                                hardware.turn_on()
                                if isinstance(hardware, Dimmer):
                                    hardware.set_pwm_level(output)
                            regulator_decrease = REGULATORS[measure].decrease
                            for hardware_uid in self._regulators[regulator_decrease]:
                                hardware = cast(
                                    Switch, self.hardware[hardware_uid])
                                hardware.turn_off()
                        elif output < 0:
                            regulator_increase = REGULATORS[measure].increase
                            for hardware_uid in self._regulators[regulator_increase]:
                                hardware = cast(
                                    Switch, self.hardware[hardware_uid])
                                hardware.turn_off()
                            regulator_decrease = REGULATORS[measure].decrease
                            for hardware_uid in self._regulators[regulator_decrease]:
                                hardware = cast(
                                    Switch, self.hardware[hardware_uid])
                                hardware.turn_on()
                                if isinstance(hardware, Dimmer):
                                    hardware.set_pwm_level(-output)
        else:
            if not self.config.get_management("sensors"):
                self.logger.error(
                    "The climate subroutine requires sensors management in order to "
                    "work. Stopping the climate subroutine."
                )
                self.stop()
            else:
                self.logger.debug(
                    f"Could not reach Sensors subroutine, climate subroutine will "
                    f"try again {5 - self._sensor_miss} times before stopping."
                )
                self._sensor_miss += 1

        if self._sensor_miss >= MISSES_BEFORE_STOP:
            self.logger.error(
                "Maximum number of Sensors data miss reached, stopping "
                "climate subroutine."
            )
            self.stop()

    def _update_climate_targets(self) -> None:
        for regulated in self._regulated:
            climate_parameter = self.config.get_climate_parameters(regulated).dict()
            self._targets[regulated] = {
                    tod: climate_parameter[tod]
                    for tod in ("day", "night")
            }

    def _start(self):
        self._update_time_parameters()
        self._update_climate_targets()
        self.logger.info(
            f"Starting climate routine. It will run every minute"
        )
        scheduler.add_job(
            self._climate_routine,
            trigger="cron", minute="*",
            id=f"{self._ecosystem_name}-climate"
        )

    def _stop(self):
        scheduler.remove_job(job_id=f"{self._ecosystem_name}-climate")
        self._reset_PIDs()
        self._reset_regulators_dict()

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
        hardware_needed = set()
        for couple in REGULATORS.values():
            for IO_type in couple:
                extra = set(self.config.get_IO_group_uids(IO_type))
                hardware_needed = hardware_needed | extra
        return hardware_needed

    def update_time_parameters(self):
        try:
            self._update_time_parameters()
        except StoppingSubroutine:
            self.stop()

    def update_climate_parameters(self) -> None:
        self._update_regulated()
        self._update_climate_targets()

    @property
    def regulated(self) -> set[ClimateParameterNames]:
        return self._regulated

    @property
    def targets(self) -> dict:
        return self._targets
