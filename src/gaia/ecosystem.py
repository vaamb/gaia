from __future__ import annotations

from datetime import date, datetime, time
import logging
import logging.config
from threading import Lock
import typing as t
import weakref

from gaia_validators import (
    ActuatorModePayload, ActuatorState, ActuatorsDataDict, BaseInfoConfig,
    ChaosConfig, Empty, EnvironmentConfig, HardwareConfig, HardwareType,
    HealthData, LightData, LightingHours, LightMethod, ManagementConfig,
    safe_enum_from_name, SensorsData)

from gaia.config import get_environment_config, SpecificEnvironmentConfig
from gaia.exceptions import StoppingEcosystem, UndefinedParameter
from gaia.subroutines import SUBROUTINES, SubroutineTypes
from gaia.subroutines.chaos import Chaos
from gaia.subroutines.climate import ClimateParameterNames, ClimateTarget


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.engine import Engine
    from gaia.events import Events
    from gaia.subroutines import Climate, Health, Light, Sensors
    subroutines: Climate | Health | Light | Sensors


lock = Lock()


def _to_dt(_time: time) -> datetime:
    # Transforms time to today's datetime. Needed to use timedelta
    _date = date.today()
    return datetime.combine(_date, _time)


def _generate_actuators_state_dict() -> ActuatorsDataDict:
    return {
        actuator: ActuatorState().dict()
        for actuator in [
            "light", "cooler", "heater", "humidifier", "dehumidifier"]
    }


class Ecosystem:
    """An Ecosystem class that manages subroutines

    The Ecosystem will take care of creating, starting and stopping the required
    subroutines that will themselves act on the physical ecosystem parameters

    :param ecosystem_id: The name or the uid of an ecosystem, as written in
                          'ecosystems.cfg'
    """
    def __init__(self, ecosystem_id: str, engine: "Engine"):
        self._config: SpecificEnvironmentConfig = get_environment_config(ecosystem_id)
        self._uid: str = self._config.uid
        self._name: str = self._config.name
        self._engine: "Engine" = weakref.proxy(engine)
        self.logger: logging.Logger = logging.getLogger(
            f"gaia.engine.{self._name}"
        )
        self.logger.info("Initializing Ecosystem")
        self._alarms: list = []
        self.lighting_hours = LightingHours(
            morning_start=self.config.time_parameters.day,
            evening_end=self.config.time_parameters.night,
        )
        self._actuators_state: ActuatorsDataDict = _generate_actuators_state_dict()
        self.subroutines:  dict[SubroutineTypes, "subroutines"] = {}
        for subroutine in SUBROUTINES:
            self.init_subroutine(subroutine)
        self._chaos: Chaos = Chaos(self, 0, 0, 1)
        self.refresh_chaos()
        self._started: bool = False
        self.logger.debug(f"Ecosystem initialization successful")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.uid}, name={self.name}, " \
               f"status={self.status}, engine={self._engine})"

    def _refresh_subroutines(self) -> None:
        # Need to start sensors and lights before other subroutines
        subroutines_ordered = set(SUBROUTINES.keys())
        subroutines_needed = subroutines_ordered.intersection(
            self._config.get_managed_subroutines()
        )
        to_stop = self.subroutines_started - subroutines_needed
        for subroutine in to_stop:
            self.stop_subroutine(subroutine)
        if not subroutines_needed:
            raise StoppingEcosystem
        for subroutine in self.subroutines_started:
            self.subroutines[subroutine].refresh_hardware()
        to_start = subroutines_needed - self.subroutines_started
        for subroutine in to_start:
            self.start_subroutine(subroutine)

    """
    API calls
    """
    @property
    def uid(self) -> str:
        return self._uid

    @property
    def name(self) -> str:
        return self._name

    @property
    def status(self) -> bool:
        return self._started

    @property
    def config(self) -> SpecificEnvironmentConfig:
        return self._config

    @property
    def engine(self) -> "Engine":
        return self._engine

    @property
    def event_handler(self) -> "Events":
        return self._engine.event_handler

    @property
    def chaos(self) -> Chaos:
        return self._chaos

    @property
    def subroutines_started(self) -> set:
        return set([
            subroutine for subroutine in self.subroutines
            if self.subroutines[subroutine].status
        ])

    @property
    def base_info(self) -> BaseInfoConfig:
        return BaseInfoConfig(
            uid=self.uid,
            name=self.name,
            status=self.status,
            engine_uid=self.engine.uid,
        )

    @property
    def light_info(self) -> LightData:
        return LightData(
            method=self.config.light_method,
            morning_start=self.config.time_parameters.day,
            evening_end=self.config.time_parameters.night,
        )

    light_data = light_info

    @property
    def light_method(self) -> LightMethod:
        try:
            return self.config.light_method
        except UndefinedParameter:
            return LightMethod.fixed

    @light_method.setter
    def light_method(self, value: LightMethod) -> None:
        if value in (LightMethod.elongate, LightMethod.mimic):
            self.refresh_lighting_hours(send=True)

    @property
    def management(self) -> ManagementConfig:
        """Return the subroutines' management corrected by whether they are
        manageable or not"""
        base_management = self.config.ecosystem_config["management"]
        management = {}
        for m in base_management:
            try:
                management[m] = self.config.get_management(m) & self.subroutines[m].manageable
            except KeyError:
                management[m] = self.config.get_management(m)
        return ManagementConfig(**management)

    @property
    def environmental_parameters(self) -> EnvironmentConfig:
        environment_dict = self.config.ecosystem_config.get("environment", {})
        return EnvironmentConfig(**environment_dict)

    @property
    def hardware(self) -> list[HardwareConfig]:
        hardware_dict = self.config.IO_dict
        return [HardwareConfig(uid=key, **value) for key, value in hardware_dict.items()]

    def init_subroutine(self, subroutine_name: SubroutineTypes) -> None:
        """Initialize a Subroutines

        :param subroutine_name: The name of the Subroutines to initialize
        """
        self.subroutines[subroutine_name] = SUBROUTINES[subroutine_name](self)

    def start_subroutine(self, subroutine_name: SubroutineTypes) -> None:
        """Start a Subroutines

        :param subroutine_name: The name of the Subroutines to start
        """
        self.subroutines[subroutine_name].start()

    def stop_subroutine(self, subroutine_name: SubroutineTypes) -> None:
        """Stop a Subroutines

        :param subroutine_name: The name of the Subroutines to stop
        """
        self.subroutines[subroutine_name].stop()

    def refresh_subroutines(self) -> None:
        """Start and stop the Subroutines based on the 'ecosystem.cfg' file"""
        for subroutine in self.subroutines.values():
            subroutine.update_manageable()
        try:
            self._refresh_subroutines()
        except StoppingEcosystem:
            if self.status:
                self.logger.info("No subroutine are running, stopping the Ecosystem")
                self.stop()

    def get_subroutine_status(self, subroutine_name: SubroutineTypes) -> bool:
        try:
            return self.subroutines[subroutine_name].status
        except KeyError:
            return False

    def refresh_chaos(self):
        try:
            values = self.config.chaos
        except UndefinedParameter:
            values = ChaosConfig()
        self.chaos.frequency = values.frequency
        self.chaos.duration = values.duration
        self.chaos.intensity = values.intensity
        self.chaos.update()

    def start(self):
        """Start the Ecosystem

        When started, the Ecosystem will automatically start and stop the
        Subroutines based on the 'ecosystem.cfg' file
        """
        if not self.status:
            try:
                self.refresh_lighting_hours()
                self.logger.info("Starting the Ecosystem")
                self._refresh_subroutines()
                if self.engine.use_message_broker and self.event_handler.registered:
                    self.event_handler.send_ecosystems_info(self.uid)
                self.logger.debug(f"Ecosystem successfully started")
                self._started = True
            except StoppingEcosystem:
                self.logger.info(
                    "The Ecosystem isn't managing any subroutine, it will stop"
                )
        else:
            raise RuntimeError(f"Ecosystem {self._name} is already running")

    def stop(self):
        """Stop the Ecosystem"""
        if self.status:
            self.logger.info("Stopping the Ecosystem ...")
            for subroutine in reversed(list(SUBROUTINES.keys())):
                self.subroutines[subroutine].stop()
            if not any([self.subroutines[subroutine].status
                        for subroutine in self.subroutines]):
                self.logger.debug("Ecosystem successfully stopped")
            else:
                self.logger.error("Failed to stop Ecosystem")
                raise Exception(f"Failed to stop Ecosystem {self._name}")
            self._started = False

    # Actuator
    @property
    def actuator_info(self) -> ActuatorsDataDict:
        return self._actuators_state

    actuator_data = actuator_info

    def turn_actuator(
            self,
            actuator: HardwareType | str,
            mode: ActuatorModePayload | str = ActuatorModePayload.automatic,
            countdown: float = 0.0
    ) -> None:
        """Turn the actuator to the specified mode

        :param actuator: the name of a type of actuators, ex: 'lights'.
        :param mode: the mode to which the actuator needs to be set. Can be
                     'on', 'off' or 'automatic'.
        :param countdown: the delay before which the actuator will be turned to
                          the specified mode.
        """
        actuator: HardwareType = safe_enum_from_name(
            HardwareType, actuator)
        mode: ActuatorModePayload = safe_enum_from_name(
            ActuatorModePayload, mode)
        try:
            if actuator == HardwareType.light:
                if self.get_subroutine_status("light"):
                    light_subroutine: "Light" = self.subroutines["light"]
                    light_subroutine.turn_light(
                        turn_to=mode, countdown=countdown)
                else:
                    raise RuntimeError
            elif actuator in [
                HardwareType.heater, HardwareType.cooler, HardwareType.humidifier,
                HardwareType.dehumidifier
            ]:
                if self.get_subroutine_status("climate"):
                    light_subroutine: "Climate" = self.subroutines["climate"]
                    light_subroutine.turn_climate_actuator(
                        climate_actuator=actuator, turn_to=mode, countdown=countdown)
                else:
                    raise RuntimeError
            else:
                raise ValueError(
                    f"Actuator '{actuator.value}' is not currently supported"
                )
        except RuntimeError:
            self.logger.error(
                f"Cannot turn {actuator} to {mode} as the subroutine managing it "
                f"is not currently running"
            )
        else:
            if self.engine.use_message_broker and self.event_handler.registered:
                try:
                    self.event_handler.send_actuator_data(
                        ecosystem_uids=[self._uid])
                except Exception as e:
                    msg = e.args[1] if len(e.args) > 1 else e.args[0]
                    if "is not a connected namespace" in msg:
                        return
                    self.logger.error(
                        f"Encountered an error while sending actuator data. "
                        f"ERROR msg: `{e.__class__.__name__} :{e}`"
                    )

    # Sensors
    @property
    def sensors_data(self) -> SensorsData | Empty:
        if self.get_subroutine_status("sensors"):
            sensors_subroutine: "Sensors" = self.subroutines["sensors"]
            return sensors_subroutine.sensors_data
        return Empty()

    # Light
    def refresh_lighting_hours(self, send=True) -> None:
        self.logger.debug("Refreshing sun times")
        time_parameters = self.config.time_parameters
        # Check we've got the info required
        # Then update info using lock as the whole dict should be transformed at the "same time"
        if self.config.light_method == LightMethod.fixed:
            with lock:
                self.lighting_hours = LightingHours(
                    morning_start=time_parameters.day,
                    evening_end=time_parameters.night,
                )

        elif self.config.light_method == LightMethod.mimic:
            if self.config.sun_times is None:
                self.logger.error(
                    "Cannot use method 'place' without sun times available. "
                    "Using 'fixed' method instead."
                )
                self.config.light_method = LightMethod.fixed
                self.refresh_lighting_hours(send=send)
            else:
                with lock:
                    self.lighting_hours = LightingHours(
                        morning_start=self.config.sun_times.sunrise,
                        evening_end=self.config.sun_times.sunset,
                    )

        elif self.config.light_method == LightMethod.elongate:
            if (
                    time_parameters.day is None
                    or time_parameters.night is None
                    or self.config.sun_times is None
            ):
                self.logger.error(
                    "Cannot use method 'elongate' without time parameters set in "
                    "config and sun times available. Using 'fixed' method instead."
                )
                self.config.light_method = LightMethod.fixed
                self.refresh_lighting_hours(send=send)
            else:
                sunrise = _to_dt(self.config.sun_times.sunrise)
                sunset = _to_dt(self.config.sun_times.sunset)
                twilight_begin = _to_dt(self.config.sun_times.twilight_begin)
                offset = sunrise - twilight_begin
                with lock:
                    self.lighting_hours = LightingHours(
                        morning_start=time_parameters.day,
                        morning_end=(sunrise + offset).time(),
                        evening_start=(sunset - offset).time(),
                        evening_end=time_parameters.night,
                    )

        if (
                send
                and self.engine.use_message_broker
                and self.event_handler.registered
        ):
            try:
                self.event_handler.send_light_data(
                    ecosystem_uids=[self._uid])
            except Exception as e:
                msg = e.args[1] if len(e.args) > 1 else e.args[0]
                if "is not a connected namespace" in msg:
                    return  # TODO: find a way to catch if many errors
                self.logger.error(
                    f"Encountered an error while sending light data. "
                    f"ERROR msg: `{e.__class__.__name__} :{e}`"
                )

    # Health
    @property
    def plants_health(self) -> HealthData | Empty:
        if self.get_subroutine_status("health"):
            health_subroutine: "Health" = self.subroutines["health"]
            return health_subroutine.plants_health
        return Empty()

    health_data = plants_health

    # Climate
    def climate_parameters_regulated(self) -> set[str]:
        if self.get_subroutine_status("climate"):
            climate_subroutine: "Climate" = self.subroutines["climate"]
            return climate_subroutine.regulated
        return set()

    def climate_targets(self) -> dict[ClimateParameterNames, ClimateTarget]:
        if self.get_subroutine_status("climate"):
            climate_subroutine: "Climate" = self.subroutines["climate"]
            return climate_subroutine.targets
        return {}
