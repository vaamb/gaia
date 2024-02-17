from __future__ import annotations

import logging
import typing
import weakref

import gaia_validators as gv

from gaia.actuator_handler import ActuatorHandler, ActuatorHub
from gaia.config import EcosystemConfig
from gaia.exceptions import NonValidSubroutine, UndefinedParameter
from gaia.subroutines import (
    Climate, Health, Light, Sensors, subroutine_dict, SubroutineDict,
    subroutine_names, SubroutineNames)
from gaia.virtual import VirtualEcosystem


if typing.TYPE_CHECKING:  # pragma: no cover
    from gaia.engine import Engine
    from gaia.events import Events


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
        self._virtual_self: VirtualEcosystem | None = None
        if self.engine.config.app_config.VIRTUALIZATION:
            virtual_cfg = self.engine.config.app_config.VIRTUALIZATION_PARAMETERS
            virtual_eco_cfg: dict = virtual_cfg.get("ecosystems", {}).get(self.uid, {})
            self._virtual_self = VirtualEcosystem(
                self.engine.virtual_world, self.uid, **virtual_eco_cfg)
        self._alarms: list = []
        self.actuator_hub: ActuatorHub = ActuatorHub(self)
        self.subroutines: SubroutineDict = {}  # noqa: the dict is filled just after
        for subroutine_name in subroutine_names:
            self.subroutines[subroutine_name] = subroutine_dict[subroutine_name](self)
        self.config.update_chaos_time_window()
        self._started: bool = False
        self.logger.debug(f"Ecosystem initialization successful")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.uid}, name={self.name}, " \
               f"status={self.started}, engine={self._engine})"

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
    def started(self) -> bool:
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
            if subroutine.started
        ])

    @property
    def base_info(self) -> gv.BaseInfoConfig:
        return gv.BaseInfoConfig(
            uid=self.uid,
            name=self.name,
            status=self.started,
            engine_uid=self.engine.uid,
        )

    @property
    def light_method(self) -> gv.LightMethod:
        return self.config.light_method

    def set_light_method(self, value: gv.LightMethod) -> None:
        self.config.set_light_method(value)

    @property
    def light_info(self) -> gv.LightData:
        return gv.LightData.from_lighting_hours(
            self.config.lighting_hours, self.config.light_method)

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

    @property
    def virtual_self(self) -> VirtualEcosystem:
        if self._virtual_self is None:
            raise AttributeError(
                "'VIRTUALIZATION' needs to be set in GaiaConfig to use virtualization.")
        return self._virtual_self

    @property
    def virtualized(self) -> bool:
        return self._virtual_self is not None

    def enable_subroutine(self, subroutine_name: SubroutineNames) -> None:
        """Enable a Subroutine

        This will mark the subroutine as managed in the configuration file.

        :param subroutine_name: The name of the Subroutine to enable
        """
        try:
            self.subroutines[subroutine_name].enable()
        except KeyError:
            raise NonValidSubroutine(f"Subroutine '{subroutine_name}' is not valid.")
        else:
            self.config.save()

    def disable_subroutine(self, subroutine_name: SubroutineNames) -> None:
        """Disable a Subroutine

        This will mark the subroutine as not managed in the configuration file.

        :param subroutine_name: The name of the Subroutine to disable
        """
        try:
            self.subroutines[subroutine_name].disable()
        except KeyError:
            raise NonValidSubroutine(f"Subroutine '{subroutine_name}' is not valid.")
        else:
            self.config.save()

    def start_subroutine(self, subroutine_name: SubroutineNames) -> None:
        """Start a Subroutine

        :param subroutine_name: The name of the Subroutine to start
        """
        try:
            self.subroutines[subroutine_name].start()
        except KeyError:
            raise NonValidSubroutine(f"Subroutine '{subroutine_name}' is not valid.")

    def stop_subroutine(self, subroutine_name: SubroutineNames) -> None:
        """Stop a Subroutine

        :param subroutine_name: The name of the Subroutine to stop
        """
        try:
            self.subroutines[subroutine_name].stop()
        except KeyError:
            raise NonValidSubroutine(f"Subroutine '{subroutine_name}' is not valid.")

    def get_subroutine_status(self, subroutine_name: SubroutineNames) -> bool:
        try:
            return self.subroutines[subroutine_name].started
        except KeyError:
            raise NonValidSubroutine(f"Subroutine '{subroutine_name}' is not valid.")

    def refresh_subroutines(self) -> None:
        """Start and stop the Subroutines based on the 'ecosystem.cfg' file"""
        self.logger.debug("Refreshing the subroutines.")
        # Need to start sensors and lights before other subroutines
        subroutines_needed = set(subroutine_names).intersection(
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
        for subroutine in subroutine_names:
            if subroutine not in to_start:
                continue
            self.logger.debug(f"Starting the subroutine '{subroutine}'")
            self.start_subroutine(subroutine)

    def start(self):
        """Start the Ecosystem

        When started, the Ecosystem will automatically start and stop the
        Subroutines based on the 'ecosystem.cfg' file
        """
        if self.started:
            raise RuntimeError(f"Ecosystem {self.name} is already running")
        self.refresh_lighting_hours()
        self.logger.info("Starting the ecosystem")
        if self.virtualized:
            self.virtual_self.start()
        self.refresh_subroutines()
        if self.engine.use_message_broker and self.event_handler.registered:
            self.event_handler.send_ecosystems_info(self.uid)
        self.logger.debug(f"Ecosystem successfully started")
        self._started = True

    def stop(self):
        """Stop the Ecosystem"""
        if not self.started:
            raise RuntimeError("Cannot stop an ecosystem that hasn't started")
        self.logger.info("Shutting down the ecosystem")
        subroutines_to_stop: list[SubroutineNames] = subroutine_names
        for subroutine in reversed(subroutines_to_stop):
            if self.subroutines[subroutine].started:
                self.subroutines[subroutine].stop()
        if not any([self.subroutines[subroutine].started
                    for subroutine in self.subroutines]):
            self.logger.debug("Ecosystem successfully stopped")
        else:
            self.logger.error("Failed to stop the ecosystem")
            raise Exception(f"Failed to stop ecosystem {self.name}")
        self._started = False

    # Chaos
    @property
    def chaos_parameters(self) -> gv.ChaosParameters:
        return self.config.chaos_parameters

    # Actuator
    @property
    def actuator_data(self) -> gv.ActuatorsDataDict:
        return self.actuator_hub.as_dict()

    def turn_actuator(
            self,
            actuator: gv.HardwareType.actuator | gv.HardwareTypeNames,
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
        assert validated_actuator in gv.HardwareType.actuator
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
            elif validated_actuator in gv.HardwareType.climate_actuator:
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
                    self.event_handler.send_payload(
                        "actuator_data", ecosystem_uids=[self._uid])
                except Exception as e:
                    msg = e.args[1] if len(e.args) > 1 else e.args[0]
                    if "is not a connected namespace" in msg:
                        return
                    self.logger.error(
                        f"Encountered an error while sending actuator data. "
                        f"ERROR msg: `{e.__class__.__name__} :{e}`"
                    )

    def get_actuator_handler(
            self,
            actuator_type: gv.HardwareType.actuator | gv.HardwareTypeNames
    ) -> ActuatorHandler:
        return self.actuator_hub.get_handler(actuator_type)

    # Sensors
    @property
    def sensors_data(self) -> gv.SensorsData | gv.Empty:
        if self.get_subroutine_status("sensors"):
            sensors_subroutine: Sensors = self.subroutines["sensors"]
            return sensors_subroutine.sensors_data
        return gv.Empty()

    # Light
    def refresh_lighting_hours(self, send: bool = True) -> None:
        self.config.refresh_lighting_hours(send=send)

    # Health
    @property
    def plants_health(self) -> gv.HealthRecord | gv.Empty:
        if self.get_subroutine_status("health"):
            health_subroutine: Health = self.subroutines["health"]
            return health_subroutine.plants_health
        return gv.Empty()

    health_data = plants_health

    # Climate
    def climate_parameters_regulated(self) -> set[gv.ClimateParameter]:
        if self.get_subroutine_status("climate"):
            climate_subroutine: Climate = self.subroutines["climate"]
            return set(climate_subroutine.regulated_parameters)
        return set()
