from __future__ import annotations

import logging
import typing
from typing import cast, Literal, overload, Self

import gaia_validators as gv

from gaia.actuator_handler import ActuatorHandler, ActuatorHub
from gaia.config import EcosystemConfig
from gaia.dependencies.camera import SerializableImage
from gaia.exceptions import HardwareNotFound, SubroutineNotFound
from gaia.hardware.abc import Hardware
from gaia.subroutines import (
    Climate, Health, Light, Pictures, Sensors, subroutine_dict, SubroutineDict,
    subroutine_names, SubroutineNames, SubroutineTemplate, Weather)
from gaia.virtual import VirtualEcosystem


if typing.TYPE_CHECKING:  # pragma: no cover
    from gaia.engine import Engine
    from gaia.events import Events


class _EcosystemPayloads:
    def __init__(self, ecosystem: Ecosystem) -> None:
        self.ecosystem = ecosystem
        self.config = ecosystem.config

    @property
    def base_info(self) -> gv.BaseInfoConfig:
        return gv.BaseInfoConfig(
            uid=self.config.uid,
            name=self.config.name,
            status=self.ecosystem.started,
            engine_uid=self.ecosystem.engine.uid,
        )

    @property
    def management(self) -> gv.ManagementConfig:
        return gv.ManagementConfig(**self.config.managements)

    @property
    def chaos_parameters(self) -> gv.ChaosParameters:
        return self.config.chaos_parameters

    @property
    def nycthemeral_info(self) -> gv.NycthemeralCycleInfo:
        return gv.NycthemeralCycleInfo(
            **self.config.nycthemeral_cycle,
            **self.config.lighting_hours.model_dump(),
        )

    @property
    def climate(self) -> list[gv.ClimateConfig]:
        return [
            gv.ClimateConfig(parameter=key, **value)
            for key, value in self.config.climate.items()
        ]

    @property
    def weather(self) -> list[gv.WeatherConfig]:
        return [
            gv.WeatherConfig(parameter=key, **value)
            for key, value in self.config.weather.items()
        ]

    @property
    def hardware(self) -> list[gv.HardwareConfig]:
        hardware_dict = self.config.IO_dict
        return [
            gv.HardwareConfig(uid=key, **value)
            for key, value in hardware_dict.items()
        ]

    @property
    def plants(self) -> list[gv.PlantConfig]:
        return [
            gv.PlantConfig(uid=key, **value)
            for key, value in self.config.plants_dict.items()
        ]

    @property
    def actuators_record(self) -> list[gv.ActuatorStateRecord]:
        return self.ecosystem.actuator_hub.as_records()

    actuators_data = actuators_record

    @property
    def sensors_data(self) -> gv.SensorsData | gv.Empty:
        return self.ecosystem.sensors_data


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
            engine: Engine | None = None,
    ) -> None:
        if engine is None:
            from gaia import Engine

            engine = Engine()
        self._engine: Engine = engine
        self._config: EcosystemConfig = \
            self.engine.config.get_ecosystem_config(ecosystem_id)
        self._uid: str = self.config.uid
        self._name: str = self.config.name
        self._payloads: _EcosystemPayloads = _EcosystemPayloads(self)
        self.logger: logging.Logger = logging.getLogger(
            f"gaia.engine.{self._name.replace(' ', '_')}")
        self.logger.info("Initializing the ecosystem.")
        self._virtual_self: VirtualEcosystem | None = None
        if self.engine.config.app_config.VIRTUALIZATION:
            virtual_cfg = self.engine.config.app_config.VIRTUALIZATION_PARAMETERS
            virtual_eco_cfg: dict = virtual_cfg.get("ecosystems", {}).get(self.uid, {})
            self._virtual_self = VirtualEcosystem(
                self, self.engine.virtual_world, **virtual_eco_cfg)
        self._hardware: dict[str, Hardware] = {}
        self._alarms: list = []
        self.actuator_hub: ActuatorHub = ActuatorHub(self)
        self.subroutines: SubroutineDict = {}  # noqa: the dict is filled just after
        for subroutine_name in subroutine_names:
            self.subroutines[subroutine_name] = subroutine_dict[subroutine_name](self)
        self._started: bool = False
        self.logger.debug("Ecosystem initialization successful.")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"{self.__class__.__name__}({self.uid}, name={self.name}, "
            f"status={self.started}, engine={self._engine})"
        )

    # ---------------------------------------------------------------------------
    #   Creation and deletion
    # ---------------------------------------------------------------------------
    async def _async_init(self) -> None:
        await self.initialize_hardware()

    @classmethod
    async def initialize(
            cls,
            ecosystem_id: str,
            engine: Engine | None = None,
    ) -> Self:
        # Sync initialization of the ecosystem
        ecosystem = cls(ecosystem_id, engine)
        # Finalization of the initialization
        try:
            await ecosystem._async_init()
        except Exception:
            await ecosystem.terminate()
            raise
        return ecosystem

    async def terminate(self) -> None:
        if self._started:
            raise RuntimeError("Cannot terminate a running ecosystem. Stop it first")
        # Terminate the subroutines first
        self.terminate_subroutines()
        # Terminate the actuator hub
        self.terminate_actuator_hub()
        # Terminate the hardware
        await self.terminate_hardware()
        # Detach the virtual ecosystem
        if self.virtualized:
            self._virtual_self = None

    # ---------------------------------------------------------------------------
    #   Properties
    # ---------------------------------------------------------------------------
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
    def engine(self) -> Engine:
        return self._engine

    @property
    def event_handler(self) -> Events:
        return self._engine.event_handler

    @property
    def virtual_self(self) -> VirtualEcosystem:
        if self._virtual_self is None:
            raise AttributeError(
                "'VIRTUALIZATION' needs to be set in GaiaConfig to use virtualization.")
        return self._virtual_self

    @property
    def virtualized(self) -> bool:
        return self._virtual_self is not None

    # ---------------------------------------------------------------------------
    #   Subroutines management
    # ---------------------------------------------------------------------------
    @overload
    def get_subroutine(self, subroutine_name: Literal["sensors"]) -> Sensors: ...
    @overload
    def get_subroutine(self, subroutine_name: Literal["light"]) -> Light: ...
    @overload
    def get_subroutine(self, subroutine_name: Literal["climate"]) -> Climate: ...
    @overload
    def get_subroutine(self, subroutine_name: Literal["weather"]) -> Weather: ...
    @overload
    def get_subroutine(self, subroutine_name: Literal["pictures"]) -> Pictures: ...
    @overload
    def get_subroutine(self, subroutine_name: Literal["health"]) -> Health: ...

    def get_subroutine(self, subroutine_name: SubroutineNames) -> SubroutineTemplate:
        try:
            return self.subroutines[subroutine_name]
        except KeyError:
            raise SubroutineNotFound(f"Subroutine '{subroutine_name}' is not valid.")

    async def enable_subroutine(self, subroutine_name: SubroutineNames) -> None:
        """Enable a Subroutine

        This will mark the subroutine as managed in the configuration file.

        :param subroutine_name: The name of the Subroutine to enable
        """
        subroutine = self.get_subroutine(subroutine_name)
        subroutine.enable()
        await self.config.save()

    async def disable_subroutine(self, subroutine_name: SubroutineNames) -> None:
        """Disable a Subroutine

        This will mark the subroutine as not managed in the configuration file.

        :param subroutine_name: The name of the Subroutine to disable
        """
        subroutine = self.get_subroutine(subroutine_name)
        subroutine.disable()
        await self.config.save()

    async def start_subroutine(self, subroutine_name: SubroutineNames) -> None:
        """Start a Subroutine

        :param subroutine_name: The name of the Subroutine to start
        """
        self.logger.debug(f"Starting the subroutine '{subroutine_name}'.")
        subroutine = self.get_subroutine(subroutine_name)
        try:
            await subroutine.start()
        except Exception as e:
            self.logger.error(
                f"Encountered an error while starting the subroutine "
                f"'{subroutine}'. ERROR msg: `{e.__class__.__name__}: {e}`."
            )

    async def stop_subroutine(self, subroutine_name: SubroutineNames) -> None:
        """Stop a Subroutine

        :param subroutine_name: The name of the Subroutine to stop
        """
        self.logger.debug(f"Stopping the subroutine '{subroutine_name}'.")
        subroutine = self.get_subroutine(subroutine_name)
        try:
            await subroutine.stop()
        except Exception as e:
            self.logger.error(
                f"Encountered an error while stopping the subroutine "
                f"'{subroutine}'. ERROR msg: `{e.__class__.__name__}: {e}`."
            )

    async def refresh_subroutine(self, subroutine_name: SubroutineNames) -> None:
        subroutine = self.get_subroutine(subroutine_name)
        try:
            await subroutine.refresh()
        except Exception as e:
            self.logger.error(
                f"Encountered an error while refreshing the subroutine "
                f"'{subroutine}'. ERROR msg: `{e.__class__.__name__}: {e}`."
            )

    def get_subroutine_status(self, subroutine_name: SubroutineNames) -> bool:
        subroutine = self.get_subroutine(subroutine_name)
        return subroutine.started

    async def refresh_subroutines(self) -> None:
        """Start and stop the Subroutines based on the 'ecosystem.cfg' file"""
        self.logger.debug("Refreshing the subroutines.")
        # Make sure the sensors and light subroutines are started first and stopped last
        def order_subroutines(to_keep: set[str]) -> list:
            return [n for n in subroutine_names if n in to_keep]

        subroutines_enabled = self._config.get_subroutines_enabled()
        subroutines_needed = set(subroutine_names).intersection(subroutines_enabled)
        if not subroutines_needed:
            self.logger.debug("No subroutine needed.")
            return

        # First, stop the subroutines not needed anymore
        to_stop = order_subroutines(self.subroutines_started - subroutines_needed)
        for subroutine_name in reversed(to_stop):
            await self.stop_subroutine(subroutine_name)
        # Then, update the subroutines already running
        for subroutine_name in order_subroutines(self.subroutines_started):
            await self.refresh_subroutine(subroutine_name)
        # Finally, start the new subroutines
        to_start = subroutines_needed - self.subroutines_started
        for subroutine in order_subroutines(to_start):
            self.logger.debug(f"Starting the subroutine '{subroutine}'.")
            await self.start_subroutine(subroutine)

    def terminate_subroutines(self) -> None:
        for subroutine_name in [*self.subroutines.keys()]:
            del self.subroutines[subroutine_name]

    @property
    def subroutines_started(self) -> set[SubroutineNames]:
        return {
            subroutine_name
            for subroutine_name, subroutine in self.subroutines.items()
            if subroutine.started
        }

    @property
    def manageable_subroutines(self) -> dict[SubroutineNames, bool]:
        """Return a dict with the manageability status of the subroutines."""
        return {
            subroutine_name: subroutine.manageable
            for subroutine_name, subroutine in self.subroutines.items()
        }

    # ---------------------------------------------------------------------------
    #   Hardware management
    # ---------------------------------------------------------------------------
    def _check_hardware_is_up_to_date(self) -> None:
        if not self.started:
            return
        hardware_needed: set[str] = set(
            hardware_uid for hardware_uid in self.config.IO_dict.keys()
            if self.config.IO_dict[hardware_uid]["active"]
        )
        hardware_existing: set[str] = set(self._hardware.keys())
        if hardware_needed != hardware_existing:
            self.logger.warning(
                "The hardware is not up to date. Run `ecosystem.refresh_hardware()` "
                "to update it.")

    @property
    def hardware(self) -> dict[str, Hardware]:
        """Return the hardware mounted (/active) in the ecosystem."""
        self._check_hardware_is_up_to_date()
        return self._hardware

    def get_hardware_group_uids(
        self,
        hardware_group: str | gv.HardwareType,
    ) -> list[str]:
        """Return the UIDs of all hardware belonging to a specific group.

        :param hardware_group: The hardware group name or HardwareType enum.
        :return: List of hardware UIDs that belong to the specified group.
        """
        if isinstance(hardware_group, gv.HardwareType):
            hardware_group = cast(str, hardware_group.name)
        return [
            uid
            for uid, hardware in self.hardware.items()
            if hardware_group in hardware.groups
        ]

    async def add_hardware(
            self,
            hardware_uid: str,
    ) -> Hardware:
        """Mount a hardware device to the ecosystem.

        :param hardware_uid: The UID of the hardware to mount, as defined in
                             the configuration.
        :return: The initialized Hardware instance.
        :raises ValueError: If the hardware is already mounted.
        """
        if hardware_uid in self.hardware:
            error_msg = f"Hardware {hardware_uid} is already mounted."
            self.logger.error(error_msg)
            raise ValueError(error_msg)
        hardware_config = self.config.get_hardware_config(hardware_uid)
        try:
            hardware: Hardware = await Hardware.initialize(hardware_config, self)
            self.logger.debug(f"Hardware {hardware.name} has been set up.")
            self.hardware[hardware.uid] = hardware
            return hardware
        except Exception as e:
            uid = hardware_config.uid
            self.logger.error(
                f"Encountered an exception while setting up hardware '{uid}'. "
                f"ERROR msg: `{e.__class__.__name__}: {e}`."
            )
            raise

    async def remove_hardware(self, hardware_uid: str) -> None:
        """Dismount a hardware device from the ecosystem.

        :param hardware_uid: The UID of the hardware to dismount.
        :raises HardwareNotFound: If the hardware is not currently mounted.
        """
        if not self.hardware.get(hardware_uid):
            error_msg = f"Hardware '{hardware_uid}' not found."
            self.logger.error(error_msg)
            raise HardwareNotFound(error_msg)

        hardware = self.hardware[hardware_uid]
        await hardware.terminate()
        del self.hardware[hardware_uid]
        self.logger.debug(f"Hardware {hardware.name} has been dismounted.")

    async def initialize_hardware(self) -> None:
        """Mount all active hardware defined in the configuration.

        This is called during ecosystem initialization to set up the initial
        hardware state.
        """
        for hardware_uid, hardware_cfg in self.config.IO_dict.items():
            if hardware_cfg["active"]:
                await self.add_hardware(hardware_uid)

    async def refresh_hardware(self) -> None:
        """Synchronize mounted hardware with the current configuration.

        This method:
        1. Dismounts hardware that is no longer in the configuration
        2. Remounts hardware whose configuration has changed
        3. Mounts newly added hardware
        4. Resets actuator handlers and PIDs to reflect hardware changes
        """
        needed: set[str] = set(
            hardware_uid for hardware_uid in self.config.IO_dict.keys()
            if self.config.IO_dict[hardware_uid]["active"]
        )
        existing: set[str] = set()
        stale: set[str] = set()
        for hardware_uid in self.hardware:
            existing.add(hardware_uid)
            in_config = self.config.IO_dict.get(hardware_uid)
            if in_config is None:
                # Hardware was removed from config, go to next
                continue
            # /!\ Do not hold a reference to hardware or its reference count will never reach 0
            current = gv.to_anonymous(self.hardware[hardware_uid].dict_repr(), "uid")
            if current != in_config:
                stale.add(hardware_uid)
        # First remove hardware not in config anymore
        for hardware_uid in existing - needed:
            await self.remove_hardware(hardware_uid)
        # Then update the staled hardware
        for hardware_uid in stale:
            await self.remove_hardware(hardware_uid)
            await self.add_hardware(hardware_uid)
        # Finally mount the missing hardware
        for hardware_uid in needed - existing:
            await self.add_hardware(hardware_uid)
        # Reset cached actuators
        for actuator_handler in self.actuator_hub.actuator_handlers.values():
            actuator_handler.reset_cached_actuators()
        # Reset the pids as the number of actuators might have changed
        for pid in self.actuator_hub.pids.values():
            pid.reset()
            pid.reset_direction()

    async def terminate_hardware(self) -> None:
        for hardware_uid in [*self.hardware.keys()]:
            hardware = self.hardware[hardware_uid]
            await hardware.terminate()
            del self.hardware[hardware_uid], hardware

    # ---------------------------------------------------------------------------
    #   Lifecycle management
    # ---------------------------------------------------------------------------
    async def start(self) -> None:
        """Start the Ecosystem

        When started, the Ecosystem will automatically start and stop the
        Subroutines based on the 'ecosystem.cfg' file
        """
        if self.started:
            raise RuntimeError(f"Ecosystem {self.name} is already running")
        self.logger.info("Starting the ecosystem.")
        try:
            # Update config
            await self.config.update_chaos_time_window()
            await self.refresh_lighting_hours()
            # Start the virtual ecosystem
            if self.virtualized:
                self.virtual_self.start()
            # Mount all the hardware
            await self.refresh_hardware()
            # Refresh subroutines
            await self.refresh_subroutines()
            # Send ecosystems info
            if self.engine.message_broker_started and self.event_handler.registered:
                await self.event_handler.send_ecosystems_info(self.uid)
        except Exception as e:
            self.logger.error(
                f"Encountered an error while starting the ecosystem. "
                f"ERROR msg: `{e.__class__.__name__}: {e}`"
            )
            subroutines_to_stop: list[SubroutineNames] = subroutine_names
            for subroutine in reversed(subroutines_to_stop):
                if self.subroutines[subroutine].started:
                    await self.subroutines[subroutine].stop()
            raise
        else:
            self.logger.debug("Ecosystem successfully started.")
            self._started = True

    async def stop(self) -> None:
        """Stop the Ecosystem"""
        if not self.started:
            raise RuntimeError("Cannot stop an ecosystem that hasn't started")
        self.logger.info("Shutting down the ecosystem.")
        subroutines_to_stop: list[SubroutineNames] = subroutine_names
        for subroutine in reversed(subroutines_to_stop):
            if self.subroutines[subroutine].started:
                await self.subroutines[subroutine].stop()
        if not any(
                self.subroutines[subroutine].started
                for subroutine in self.subroutines
        ):
            self.logger.debug("Ecosystem successfully stopped.")
        else:
            self.logger.error("Failed to stop the ecosystem.")
            raise RuntimeError(f"Failed to stop ecosystem {self.name}")
        self._started = False

    # ---------------------------------------------------------------------------
    #   Config and specific subroutines interaction
    # ---------------------------------------------------------------------------
    async def set_lighting_method(
            self,
            value: gv.LightMethod,
            send_info: bool = True,
    ) -> None:
        await self.config.set_lighting_method(value)
        if send_info and self.engine.message_broker_started:
            try:
                await self.engine.event_handler.send_payload_if_connected(
                    "nycthemeral_info", ecosystem_uids=[self.uid])
            except Exception as e:
                self.logger.error(
                    f"Encountered an error while sending light data. "
                    f"ERROR msg: `{e.__class__.__name__}: {e}`"
                )

    # Actuator
    @property
    def actuators_state(self) -> dict[str, gv.ActuatorStateDict]:
        return self.actuator_hub.as_dict()

    async def turn_actuator(
            self,
            actuator: str,
            mode: gv.ActuatorModePayload | str = gv.ActuatorModePayload.automatic,
            level: float = 100.0,
            countdown: float = 0.0,
    ) -> None:
        """Turn the actuator to the specified mode

        :param actuator: the name of an actuator group, ex: 'light'.
        :param mode: the mode to which the actuator needs to be set. Can be
                     'on', 'off' or 'automatic'.
        :param level: the level to which the actuator needs to be set. Can be
                      a float between 0 and 100.
        :param countdown: the delay before which the actuator will be turned to
                          the specified mode.
        """
        if isinstance(actuator, gv.HardwareType):
            actuator = actuator.name
        # Get actuator handler
        actuator_handler = self.actuator_hub.actuator_handlers.get(actuator)
        if not actuator_handler:
            raise ValueError(
                f"Actuator group '{actuator}' is not mounted. No subroutine "
                f"managing it is currently running."
            )
        validated_mode: gv.ActuatorModePayload = \
            gv.safe_enum_from_name(gv.ActuatorModePayload, mode)
        async with actuator_handler.update_status_transaction():
            await actuator_handler.turn_to(
                turn_to=validated_mode, level=level, countdown=countdown)

    def get_actuator_handler(
            self,
            actuator_group: str,
    ) -> ActuatorHandler:
        return self.actuator_hub.get_handler(actuator_group)

    def terminate_actuator_hub(self) -> None:
        # Terminate actuator handlers
        for handler_group in [*self.actuator_hub.actuator_handlers.keys()]:
            handler = self.actuator_hub.actuator_handlers[handler_group]
            handler.reset_cached_actuators()
            # Make sure all the handlers where deactivated when the subroutine finished
            assert not handler.active
            del self.actuator_hub.actuator_handlers[handler_group], handler
        # Terminate PIDs
        for pid_group in [*self.actuator_hub.pids.keys()]:
            del self.actuator_hub.pids[pid_group]

    # Sensors
    @property
    def sensors_data(self) -> gv.SensorsData | gv.Empty:
        if self.get_subroutine_status("sensors"):
            sensors_subroutine: Sensors = self.subroutines["sensors"]
            return sensors_subroutine.sensors_data
        return gv.Empty()

    # Light
    async def refresh_lighting_hours(self, send_info: bool = True) -> None:
        await self.config.refresh_lighting_hours(send_info=send_info)

    # Health
    @property
    def plants_health(self) -> list[gv.HealthRecord] | gv.Empty:
        if self.get_subroutine_status("health"):
            health_subroutine: Health = self.subroutines["health"]
            return health_subroutine.plants_health
        return gv.Empty()

    health_data = plants_health

    # Climate
    @property
    def regulated_climate_parameters(self) -> set[gv.ClimateParameter]:
        if self.get_subroutine_status("climate"):
            climate_subroutine: Climate = self.subroutines["climate"]
            return set(climate_subroutine.regulated_parameters)
        return set()

    # Picture
    @property
    def picture_arrays(self) -> list[SerializableImage] | gv.Empty:
        if self.get_subroutine_status("pictures"):
            picture_subroutine: Pictures = self.subroutines["pictures"]
            arrays = picture_subroutine.picture_arrays
            if arrays:
                return arrays
        return gv.Empty()
