from __future__ import annotations

from datetime import date, datetime, time
import logging
from threading import Lock
import typing
import weakref

import gaia_validators as gv

from gaia.config import EcosystemConfig
from gaia.exceptions import UndefinedParameter
from gaia.subroutines import (
    Climate, Health, Light, Sensors, subroutine_dict, SubroutineDict, SubroutineNames)
from gaia.subroutines.climate import ClimateParameterNames, ClimateTarget


if typing.TYPE_CHECKING:  # pragma: no cover
    from gaia.engine import Engine
    from gaia.events import Events


def _to_dt(_time: time) -> datetime:
    # Transforms time to today's datetime. Needed to use timedelta
    _date = date.today()
    return datetime.combine(_date, _time)


def _generate_actuators_state_dict() -> gv.ActuatorsDataDict:
    return {
        actuator: gv.ActuatorState().model_dump()
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
    def __init__(
            self,
            ecosystem_id: str,
            engine: "Engine" | None = None
    ) -> None:
        if engine is None:
            from gaia import Engine
            engine = Engine()
        self._engine: "Engine" = weakref.proxy(engine)
        self._config: EcosystemConfig = \
            self.engine.config.get_ecosystem_config(ecosystem_id)
        self._uid: str = self.config.uid
        self._name: str = self.config.name
        self.logger: logging.Logger = logging.getLogger(
            f"gaia.engine.{self._name.replace(' ', '_')}")
        self.logger.info("Initializing the ecosystem")
        self._alarms: list = []
        self._lighting_hours = gv.LightingHours(
            morning_start=self.config.time_parameters.day,
            evening_end=self.config.time_parameters.night,
        )
        self.lighting_hours_lock = Lock()
        self.actuators_state: gv.ActuatorsDataDict = _generate_actuators_state_dict()
        self.subroutines: SubroutineDict = {}  # noqa: the dict is filled just after
        for subroutine_name in subroutine_dict:
            subroutine_name = typing.cast(SubroutineNames, subroutine_name)
            self.subroutines[subroutine_name] = subroutine_dict[subroutine_name](self)
        if self.engine.config.app_config.TESTING:
            from gaia.subroutines.dummy import Dummy
            self.subroutines["dummy"] = Dummy(self)
        self.config.update_chaos_time_window()
        self._started: bool = False
        self.logger.debug(f"Ecosystem initialization successful")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.uid}, name={self.name}, " \
               f"status={self.status}, engine={self._engine})"

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
    def config(self) -> EcosystemConfig:
        return self._config

    @property
    def engine(self) -> "Engine":
        return self._engine

    @property
    def event_handler(self) -> "Events":
        return self._engine.event_handler

    @property
    def subroutines_started(self) -> set[SubroutineNames]:
        return set([  # noqa
            subroutine_name for subroutine_name, subroutine in self.subroutines.items()
            if subroutine.status
        ])

    @property
    def base_info(self) -> gv.BaseInfoConfig:
        return gv.BaseInfoConfig(
            uid=self.uid,
            name=self.name,
            status=self.status,
            engine_uid=self.engine.uid,
        )

    @property
    def light_method(self) -> gv.LightMethod:
        try:
            return self.config.light_method
        except UndefinedParameter:
            return gv.LightMethod.fixed

    def set_light_method(self, value: gv.LightMethod) -> None:
        self.config.set_light_method(value)
        self.refresh_lighting_hours(send=True)

    @property
    def lighting_hours(self) -> gv.LightingHours:
        with self.lighting_hours_lock:
            return self._lighting_hours

    @lighting_hours.setter
    def lighting_hours(self, value: gv.LightingHours) -> None:
        with self.lighting_hours_lock:
            self._lighting_hours = value

    @property
    def light_info(self) -> gv.LightData:
        return gv.LightData(
            method=self.config.light_method,
            morning_start=self.config.time_parameters.day,
            evening_end=self.config.time_parameters.night,
        )

    light_data = light_info

    @property
    def management(self) -> gv.ManagementConfig:
        """Return a dict with the functionalities management status."""
        return gv.ManagementConfig(**self.config.managements)

    @property
    def manageable_subroutines(self) -> dict:
        """Return a dict with the manageability status of the subroutines."""
        return {
            subroutine_name: subroutine.manageable
            for subroutine_name, subroutine in self.subroutines.items()
        }

    @property
    def environmental_parameters(self) -> gv.EnvironmentConfig:
        environment_dict = self.config.environment
        return gv.EnvironmentConfig(**environment_dict)

    @property
    def hardware(self) -> list[gv.HardwareConfig]:
        hardware_dict = self.config.IO_dict
        return [
            gv.HardwareConfig(uid=key, **value)
            for key, value in hardware_dict.items()
        ]

    def enable_subroutine(self, subroutine_name: SubroutineNames) -> None:
        """Enable a Subroutine

        This will mark the subroutine as managed in the configuration file.

        :param subroutine_name: The name of the Subroutine to enable
        """
        self.subroutines[subroutine_name].enable()
        self.config.save()

    def disable_subroutine(self, subroutine_name: SubroutineNames) -> None:
        """Disable a Subroutine

        This will mark the subroutine as not managed in the configuration file.

        :param subroutine_name: The name of the Subroutine to disable
        """
        self.subroutines[subroutine_name].disable()
        self.config.save()

    def start_subroutine(self, subroutine_name: SubroutineNames) -> None:
        """Start a Subroutine

        :param subroutine_name: The name of the Subroutine to start
        """
        self.subroutines[subroutine_name].start()

    def stop_subroutine(self, subroutine_name: SubroutineNames) -> None:
        """Stop a Subroutine

        :param subroutine_name: The name of the Subroutine to stop
        """
        self.subroutines[subroutine_name].stop()

    def get_subroutine_status(self, subroutine_name: SubroutineNames) -> bool:
        return self.subroutines[subroutine_name].status

    def refresh_subroutines(self) -> None:
        """Start and stop the Subroutines based on the 'ecosystem.cfg' file"""
        self.logger.debug("Refreshing the subroutines.")
        # Need to start sensors and lights before other subroutines
        subroutines_ordered = set(subroutine_dict.keys())
        subroutines_needed = subroutines_ordered.intersection(
            self._config.get_subroutines_enabled()
        )
        if not subroutines_needed:
            self.logger.debug("No subroutine needed.")
            return
        # Stop the unneeded subroutines first.
        to_stop = self.subroutines_started - subroutines_needed
        for subroutine in to_stop:
            self.logger.debug(f"Stopping the subroutine '{subroutine}'")
            self.stop_subroutine(subroutine)
        # Then update the already running subroutines
        for subroutine in self.subroutines_started:
            self.subroutines[subroutine].refresh_hardware()
        # Finally, start the new subroutines
        to_start = subroutines_needed - self.subroutines_started
        for subroutine in to_start:
            self.logger.debug(f"Starting the subroutine '{subroutine}'")
            self.start_subroutine(subroutine)

    def start(self):
        """Start the Ecosystem

        When started, the Ecosystem will automatically start and stop the
        Subroutines based on the 'ecosystem.cfg' file
        """
        if not self.status:
            self.refresh_lighting_hours()
            self.logger.info("Starting the ecosystem")
            self.refresh_subroutines()
            if self.engine.use_message_broker and self.event_handler.registered:
                self.event_handler.send_ecosystems_info(self.uid)
            self.logger.debug(f"Ecosystem successfully started")
            self._started = True
        else:
            raise RuntimeError(f"Ecosystem {self.name} is already running")

    def stop(self):
        """Stop the Ecosystem"""
        if self.status:
            self.logger.info("Shutting down the ecosystem")
            subroutines_to_stop: list[SubroutineNames] = [*subroutine_dict.keys()]
            for subroutine in reversed(subroutines_to_stop):
                self.subroutines[subroutine].stop()
            if not any([self.subroutines[subroutine].status
                        for subroutine in self.subroutines]):
                self.logger.debug("Ecosystem successfully stopped")
            else:
                self.logger.error("Failed to stop the ecosystem")
                raise Exception(f"Failed to stop ecosystem {self.name}")
            self._started = False
        else:
            raise RuntimeError("Cannot stop an ecosystem that hasn't started")

    # Actuator
    @property
    def actuator_data(self) -> gv.ActuatorsDataDict:
        return self.actuators_state

    def turn_actuator(
            self,
            actuator: gv.HardwareType | str,
            mode: gv.ActuatorModePayload | str = gv.ActuatorModePayload.automatic,
            countdown: float = 0.0
    ) -> None:
        """Turn the actuator to the specified mode

        :param actuator: the name of a type of actuators, ex: 'lights'.
        :param mode: the mode to which the actuator needs to be set. Can be
                     'on', 'off' or 'automatic'.
        :param countdown: the delay before which the actuator will be turned to
                          the specified mode.
        """
        validated_actuator: gv.HardwareType = \
            gv.safe_enum_from_name(gv.HardwareType, actuator)
        validated_mode: gv.ActuatorModePayload = \
            gv.safe_enum_from_name(gv.ActuatorModePayload, mode)
        try:
            if validated_actuator == gv.HardwareType.light:
                if self.get_subroutine_status("light"):
                    light_subroutine: Light = self.subroutines["light"]
                    light_subroutine.turn_light(
                        turn_to=validated_mode, countdown=countdown)
                else:
                    raise ValueError("Light subroutine is not running")
            elif validated_actuator in [
                gv.HardwareType.heater, gv.HardwareType.cooler,
                gv.HardwareType.humidifier, gv.HardwareType.dehumidifier
            ]:
                if self.get_subroutine_status("climate"):
                    climate_subroutine: Climate = self.subroutines["climate"]
                    climate_subroutine.turn_climate_actuator(
                        climate_actuator=validated_actuator, turn_to=validated_mode,
                        countdown=countdown)
                else:
                    raise ValueError("Climate subroutine is not running")
            else:
                raise ValueError(
                    f"Actuator '{validated_actuator.value}' is not currently supported"
                )
        except RuntimeError:
            self.logger.error(
                f"Cannot turn {validated_actuator} to {validated_mode} as the subroutine managing it "
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
    def sensors_data(self) -> gv.SensorsData | gv.Empty:
        if self.get_subroutine_status("sensors"):
            sensors_subroutine: Sensors = self.subroutines["sensors"]
            return sensors_subroutine.sensors_data
        return gv.Empty()

    # Light
    def refresh_lighting_hours(self, send: bool = True) -> None:
        self.logger.debug("Refreshing sun times")
        time_parameters = self.config.time_parameters
        # Check we've got the info required
        # Then update info using lock as the whole dict should be transformed at the "same time"
        if self.config.light_method == gv.LightMethod.fixed:
            self.lighting_hours = gv.LightingHours(
                morning_start=time_parameters.day,
                evening_end=time_parameters.night,
            )

        elif self.config.light_method == gv.LightMethod.mimic:
            if self.config.sun_times is None:
                self.logger.warning(
                    "Cannot use lighting method 'place' without sun times available. "
                    "Using 'fixed' method instead."
                )
                self.config.set_light_method(gv.LightMethod.fixed)
                self.refresh_lighting_hours(send=send)
            else:
                self.lighting_hours = gv.LightingHours(
                    morning_start=self.config.sun_times.sunrise,
                    evening_end=self.config.sun_times.sunset,
                )

        elif self.config.light_method == gv.LightMethod.elongate:
            if (
                    time_parameters.day is None
                    or time_parameters.night is None
                    or self.config.sun_times is None
            ):
                self.logger.warning(
                    "Cannot use lighting method 'elongate' without time parameters set in "
                    "config and sun times available. Using 'fixed' method instead."
                )
                self.config.set_light_method(gv.LightMethod.fixed)
                self.refresh_lighting_hours(send=send)
            else:
                sunrise = _to_dt(self.config.sun_times.sunrise)
                sunset = _to_dt(self.config.sun_times.sunset)
                twilight_begin = _to_dt(self.config.sun_times.twilight_begin)
                offset = sunrise - twilight_begin
                self.lighting_hours = gv.LightingHours(
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
    def plants_health(self) -> gv.HealthRecord | gv.Empty:
        if self.get_subroutine_status("health"):
            health_subroutine: Health = self.subroutines["health"]
            return health_subroutine.plants_health
        return gv.Empty()

    health_data = plants_health

    # Climate
    def climate_parameters_regulated(self) -> set[str]:
        if self.get_subroutine_status("climate"):
            climate_subroutine: Climate = self.subroutines["climate"]
            return climate_subroutine.regulated
        return set()

    def climate_targets(self) -> dict[ClimateParameterNames, ClimateTarget]:
        if self.get_subroutine_status("climate"):
            climate_subroutine: Climate = self.subroutines["climate"]
            return climate_subroutine.targets
        return {}
