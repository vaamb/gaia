from collections import namedtuple
from datetime import datetime, time
from threading import Event
import typing as t

from simple_pid import PID

from ..exceptions import StoppingSubroutine, UndefinedParameter
from ..hardware import ACTUATORS, gpioDimmable, gpioSwitch
from ..shared_resources import scheduler
from ..subroutines.template import SubroutineTemplate


if t.TYPE_CHECKING:  # pragma: no cover
    from .light import Light
    from .sensors import Sensors


RegulatorCouple = namedtuple("regulator_couple", ["increase", "decrease"])


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
        self._refresh_PIDs()
        self._refresh_hardware_dict()
        self._regulated: t.Set[str] = set()
        self._targets: dict[str, dict[str, float]] = {}
        self._sun_times: dict[str, time] = {
            "morning_start": time(8, 0),
            "evening_end": time(8, 0)
        }
        self._finish__init__()

    def _refresh_hardware_dict(self) -> None:
        self.hardware: dict[str, dict[str, gpioSwitch]] = {
            "heaters": {},
            "coolers": {},
            "humidifiers": {},
            "dehumidifiers": {},
        }

    def _refresh_PIDs(self) -> None:
        for climate_param in REGULATORS:
            self._pids[climate_param] = PID(Kp, Ki, Kd, output_limits=(-100, 100))

    def _update_regulated(self) -> None:
        regulated: t.Set[str] = set()
        # Check if target values in config
        for climate_param in ("temperature", "humidity", "wind"):
            try:
                self.config.get_climate_parameters(climate_param)
            except UndefinedParameter:
                pass
            else:
                regulated.add(climate_param)
        if not regulated:
            self._regulated = []
            self.logger.debug(
                "No climate parameter found."
            )
            return

        # Check if regulators available
        regulators: list[str] = []
        for climate_param in REGULATORS:
            if climate_param in regulated:
                for regulator in REGULATORS[climate_param]:
                    if self.config.get_IO_group(regulator):
                        regulators.append(regulator)
        if not regulators:
            self._regulated = []
            self.logger.debug(
                "No climate hardware detected."
            )
            return
        for climate_param in regulated.copy():
            if not any([r in REGULATORS[climate_param] for r in regulators]):
                regulated.remove(climate_param)

        # Check if Sensors taking regulated params are available
        measures: t.Set[str] = set()
        sensor_uid = self.config.get_IO_group("sensor")
        for uid in sensor_uid:
            hardware = self.config.get_IO(uid)
            m = hardware.get("measure", [])
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
                for tod in ("morning_start", "evening_end"):
                    self._sun_times[tod] = \
                        light_subroutine.lighting_hours[tod]
            except (AttributeError, KeyError):
                self.logger.error(
                    "Could not obtain time parameters from the Light subroutine, "
                    "using the config ones instead."
                )
        else:
            try:
                self._sun_times["morning_start"] = \
                    self.config.time_parameters["day"]
                self._sun_times["evening_end"] = \
                    self.config.time_parameters["night"]
            except UndefinedParameter:
                self.logger.error(
                    f"No day and night parameters set for ecosystem "
                    f"{self._ecosystem_name}. Stopping the climate subroutine."
                )
                raise StoppingSubroutine

    def _climate_routine(self) -> None:
        if self.ecosystem.get_subroutine_status("sensors"):
            sensors_subroutine: "Sensors" = self.ecosystem.subroutines["sensors"]
            average = sensors_subroutine.sensors_data.get("average")
            if not average:
                self.logger.debug(
                    f"No sensor data found, climate subroutine will try "
                    f"again {5 - self._sensor_miss} times before stopping."
                )
                self._sensor_miss += 1
            else:
                for data in average:
                    measure = data["name"]
                    if measure in self._regulated:
                        value = data["value"]
                        now = datetime.now().astimezone().time()
                        tod = "day" if (
                                    self._sun_times["morning_start"] < now <=
                                    self._sun_times[
                                        "morning_start"]) else "night"
                        target = self._targets[measure][
                                     tod] * self.ecosystem.chaos.factor
                        hysteresis = self._targets[measure].get("hysteresis",
                                                                0)
                        self._pids[measure].setpoint = target
                        if abs(target - value) < hysteresis:
                            self._pids[
                                measure].reset()  # Keep reset or not? Risk of integral windup if not
                            output = 0
                        else:
                            output = self._pids[measure](value)
                        if output > 0:
                            hardware_type = f"{REGULATORS[measure].increase}s"
                            for hardware in self.hardware[hardware_type].values():
                                hardware.turn_on()
                                if isinstance(hardware, gpioDimmable):
                                    hardware.set_pwm_level(output)
                            hardware_type = f"{REGULATORS[measure].decrease}s"
                            for hardware in self.hardware[hardware_type].values():
                                hardware.turn_off()
                        elif output < 0:
                            hardware_type = f"{REGULATORS[measure].increase}s"
                            for hardware in self.hardware[hardware_type].values():
                                hardware.turn_off()
                            hardware_type = f"{REGULATORS[measure].decrease}s"
                            for hardware in self.hardware[hardware_type].values():
                                hardware.turn_on()
                                if isinstance(hardware, gpioDimmable):
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
            self._targets[regulated] = {
                    tod: self.config.get_climate_parameters(regulated)[tod]
                    for tod in ("day", "night")
            }

    def _start(self):
        self._update_time_parameters()
        self._update_climate_targets()
        self.refresh_hardware()
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
        self._refresh_PIDs()
        self._refresh_hardware_dict()

    """API calls"""
    def add_hardware(self, hardware_dict: dict):
        hardware_uid = list(hardware_dict.keys())[0]
        try:
            hardware_dict[hardware_uid]["level"] = "environment"
            hardware = self._add_hardware(hardware_dict, ACTUATORS)
            hardware.turn_off()
            self.hardware[f"{hardware.type}s"][hardware_uid] = hardware
            self.logger.debug(f"Regulator '{hardware.name}' has been set up")
            return hardware
        except Exception as e:
            self.logger.error(
                f"Encountered an exception while setting up regulator "
                f"'{hardware_uid}'. ERROR msg: `{e.__class__.__name__}: {e}`."
            )

    def remove_hardware(self, hardware_uid: str) -> None:
        for regulator_type in self.hardware:
            if hardware_uid in self.hardware[regulator_type]:
                del self.hardware[regulator_type][hardware_uid]
                return
        self.logger.error(f"Regulator '{hardware_uid}' does not exist")

    def refresh_hardware(self) -> None:
        self.update_climate_parameters()
        for regulator_type in self.hardware:
            IO_type = regulator_type[:-1] if regulator_type.endswith("s") \
                else regulator_type
            regulators_needed = set(self.config.get_IO_group(IO_type))
            regulators_existing = set(self.hardware[regulator_type])
            for hardware_uid in regulators_needed - regulators_existing:
                self.add_hardware(
                    {hardware_uid: self.config.IO_dict[hardware_uid]}
                )
            for hardware_uid in regulators_existing - regulators_needed:
                self.remove_hardware(hardware_uid)

    def update_time_parameters(self):
        try:
            self._update_time_parameters()
        except StoppingSubroutine:
            self.stop()

    def update_climate_parameters(self) -> None:
        self._update_regulated()
        self._update_climate_targets()

    @property
    def regulated(self) -> t.Set[str]:
        return self._regulated

    @property
    def targets(self) -> dict:
        return self._targets
