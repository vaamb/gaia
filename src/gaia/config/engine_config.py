from __future__ import annotations

import asyncio
from asyncio import Event, Lock, Task
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from json.decoder import JSONDecodeError
import logging
from pathlib import Path
import sys
from typing import Type
from weakref import WeakValueDictionary

from anyio.to_thread import run_sync
import pydantic

import gaia_validators as gv
from gaia_validators import safe_enum_from_name
from gaia_validators.utils import get_sun_times

from gaia.config._utils import configure_logging, GaiaConfig, GaiaConfigHelper
from gaia.config.base import BaseConfig
from gaia.config.ecosystem_config import _MetaEcosystemConfig, EcosystemConfig
from gaia.config.helpers import (
    dump_json, dump_yaml, event_wait, file_checksum, format_pydantic_error, load_json,
    load_yaml, remove_missing, validate_from_root_model)
from gaia.config.models import (
    CacheType, ChaosMemory, ChaosMemoryRootValidator, ChaosMemoryValidator,
    ConfigType, ConfigValidationError, CoordinatesDict, DEFAULT_PLACE,
    EcosystemBaseUpdateDict, EcosystemConfigDict, EcosystemConfigValidator,
    PrivateConfigDict, PrivateConfigValidator, RootClimateValidator,
    RootEcosystemsConfigValidator, RootHardwareValidator, RootPlantsValidator,
    SunTimesCacheData)
from gaia.exceptions import EcosystemNotFound, UndefinedParameter
from gaia.hardware import Hardware
from gaia.hardware.multiplexers import Multiplexer
from gaia.utils import create_uid, humanize_list, SingletonMeta

if sys.version_info < (3, 12):
    from typing_extensions import Unpack
else:
    from typing import Unpack


NORTHERN_SUMMER_START_MONTH = 3
NORTHERN_SUMMER_END_MONTH = 9


class ChecksumTracker:
    """Tracks file checksums for change detection."""

    def __init__(self):
        self._checksums: dict[Path, bytes] = {}

    async def compute(self, path: Path) -> bytes:
        """Compute checksum (no lock needed - just reads file)."""
        return await run_sync(file_checksum, path)

    async def update(self, path: Path, hash_: bytes | None = None) -> None:
        """Update stored checksum for a path."""
        if hash_ is None:
            hash_ = await self.compute(path)
        self._checksums[path] = hash_

    async def has_changed(self, path: Path) -> bool:
        """Check if file changed since last update."""
        current = await self.compute(path)
        stored = self._checksums.get(path)
        return stored is None or current != stored

    async def get_changed(self) -> set[Path]:
        """Return paths that have changed."""
        return {p for p in self._checksums if await self.has_changed(p)}


class ConfigWatchdog:
    def __init__(self, engine_config: EngineConfig, checksum_tracker: ChecksumTracker) -> None:
        self._engine_config = engine_config
        self._checksum_tracker = checksum_tracker
        self.logger = logging.getLogger("gaia.engine.config.watchdog")
        self.logger.debug("Initializing ConfigWatchdog")
        self._stop_event = Event()
        self._task: Task | None = None
        # An Event rather than a Condition so that changes detected while the
        #  engine is refreshing the ecosystems are not lost
        self.new_config = Event()

    @property
    def started(self) -> bool:
        return self._task is not None

    async def _routine(self) -> None:
        changed_paths = await self._checksum_tracker.get_changed()
        if changed_paths:
            private_path = self._engine_config.get_file_path(ConfigType.private)
            ecosystems_path = self._engine_config.get_file_path(ConfigType.ecosystems)

            if private_path in changed_paths:
                self.logger.info(
                    "Change in private configuration file detected. Updating it.")
                await self._engine_config.load(ConfigType.private)
            if ecosystems_path in changed_paths:
                self.logger.info(
                    "Change in ecosystems configuration file detected. Updating it.")
                await self._engine_config.load(ConfigType.ecosystems)
            # This unblocks the engine loop. It will then refresh ecosystems,
            #  update sun times, ecosystem lighting hours and send the data if
            #  it is connected.
            self.new_config.set()

    async def _loop(self) -> None:
        sleep_period = self._engine_config.app_config.CONFIG_WATCHER_PERIOD / 1000
        self.logger.info(
            f"Starting watchdog loop with an interval of {sleep_period:.3f} s.")
        while not self._stop_event.is_set():
            try:
                await self._routine()
            except Exception as e:  # pragma: no cover
                self.logger.error(f"Watchdog error: `{e.__class__.__name__}: {e}`.")
            await event_wait(self._stop_event, sleep_period)

    def start(self) -> None:
        if not self._engine_config.configs_loaded:  # pragma: no cover
            raise RuntimeError(
                "Configuration files need to be loaded in order to start "
                "the config files watchdog. To do so, use the "
                "`EngineConfig().initialize_configs()` method."
            )

        if self.started:  # pragma: no cover
            raise RuntimeError("Watchdog already running")

        self._stop_event.clear()
        self.logger.info("Starting the configuration files watchdog.")
        self._task = asyncio.create_task(self._loop(), name="config-watchdog")
        self.logger.debug("Configuration files watchdog successfully started.")

    def stop(self) -> None:
        if not self.started:  # pragma: no cover
            raise RuntimeError("Watchdog is not running")

        self.logger.info("Stopping the configuration files watchdog.")
        self._stop_event.set()
        self._task = None
        self.logger.debug("Configuration files watchdog successfully stopped.")


class EngineConfig(metaclass=SingletonMeta):
    """Class to interact with the configuration files

    To interact with a specific ecosystem configuration, the EcosystemConfig
    class should be used.
    """
    def __init__(self, gaia_config: Type[BaseConfig] | None = None) -> None:
        self.logger = logging.getLogger("gaia.engine.config")
        self.logger.debug("Initializing EngineConfig")
        if gaia_config is not None:
            if GaiaConfigHelper.config_is_set():
                raise ValueError(
                    "Parameter 'gaia_config' should only be given if "
                    "'GaiaConfigHelper.set_config' has not been used before.")
            GaiaConfigHelper.set_config(gaia_config)
        self._app_config = GaiaConfigHelper.get_config()
        configure_logging(self.app_config)
        self._ecosystems_config_dict: dict[str, EcosystemConfigDict] = {}
        self._private_config: PrivateConfigDict = PrivateConfigValidator().model_dump()
        self._sun_times: dict[str, SunTimesCacheData] = {}
        self._chaos_memory: dict[str, ChaosMemory] = {}
        # Watchdog threading securities
        self._checksum_tracker = ChecksumTracker()
        self._config_files_lock = Lock()
        self._watchdog: ConfigWatchdog = ConfigWatchdog(self, self._checksum_tracker)
        self.configs_loaded: bool = False

    def __repr__(self) -> str:  # pragma: no cover
        return f"EngineConfig(watchdog={self.started})"

    # ---------------------------------------------------------------------------
    #   Properties and common utilities
    # ---------------------------------------------------------------------------
    @property
    def watchdog(self) -> ConfigWatchdog:
        return self._watchdog

    @property
    def started(self) -> bool:
        return self.watchdog.started

    @property
    def new_config(self) -> Event:
        return self.watchdog.new_config

    @property
    def app_config(self) -> GaiaConfig:
        return self._app_config

    @property
    def gaia_dir(self) -> Path:
        return self.app_config.get_path("DIR")

    @property
    def logs_dir(self) -> Path:
        return self.app_config.get_path("LOG_DIR")

    @property
    def cache_dir(self) -> Path:
        return self.app_config.get_path("CACHE_DIR")

    def get_file_path(self, file_type: ConfigType | CacheType) -> Path:
        if isinstance(file_type, ConfigType):
            return self.gaia_dir / file_type.value
        if isinstance(file_type, CacheType):
            return self.cache_dir / file_type.value
        raise ValueError(f"Invalid file type: {file_type}")

    # ---------------------------------------------------------------------------
    #   Config and caches load and save
    # ---------------------------------------------------------------------------
    @property
    def config_files_lock(self) -> Lock:
        return self._config_files_lock

    def _check_files_lock_acquired(self) -> None:
        if not self._config_files_lock.locked():
            raise RuntimeError(
                "This method must be called within a "
                "`async with engine_config.config_files_lock:` block"
            )

    def _log_nycthemeral_method_issues(
            self,
            method: gv.LightingMethod | gv.NycthemeralSpanMethod,
            ecosystem_cfg: EcosystemConfigDict,
    ) -> None:
        """Validate method and log warning if it will fall back to 'fixed'."""
        try:
            EcosystemConfig.validate_nycthemeral_method(
                method, ecosystem_cfg, self.private_config["places"])
        except ConfigValidationError as e:
            method_name = "Lighting" if isinstance(method, gv.LightingMethod) else "Nycthemeral span"
            ecosystem_name = ecosystem_cfg["name"]
            self.logger.warning(
                f"{method_name} method cannot be set to '{method.name}' for "
                f"ecosystem {ecosystem_name}. Will fall back to 'fixed'. "
                f"ERROR msg: `{e.__class__.__name__}: {e}`"
            )

    @staticmethod
    def _validate_ecosystem_dict(
            ecosystem_dict: dict[str, EcosystemConfigDict],
    ) -> dict[str, EcosystemConfigDict]:
        return validate_from_root_model(ecosystem_dict, RootEcosystemsConfigValidator)

    def _validate_ecosystems_logic(
            self, ecosystem_configs: dict[str, EcosystemConfigDict],
    ) -> dict[str, EcosystemConfigDict]:
        unfixable_error: bool = False
        for ecosystem_uid, ecosystem_cfg in ecosystem_configs.items():
            ecosystem_cfg: EcosystemConfigDict
            ecosystem_name: str = ecosystem_cfg["name"]
            # Check hardware config
            self.logger.debug(
                f"Checking hardware config for ecosystem {ecosystem_name}.")
            addresses_used: list[str] = []
            for hardware_uid, hardware_dict in ecosystem_cfg["hardware"].items():
                hardware_name: str = hardware_dict["name"]
                self.logger.debug(
                    f"Checking hardware config for hardware {hardware_name} in "
                    f"ecosystem {ecosystem_name}.")
                try:
                    EcosystemConfig.validate_hardware_dict(
                        gv.to_identified(hardware_dict, {"uid": hardware_uid}),
                        addresses_used=addresses_used,
                    )
                except ValueError as e:
                    self.logger.error(
                        f"Could not validate hardware config for hardware {hardware_name} "
                        f"in ecosystem {ecosystem_name}. ERROR msg(s): `{e}`.")
                    unfixable_error = True
                else:
                    self.logger.debug(
                        f"Hardware config for {hardware_name} in ecosystem "
                        f"{ecosystem_name} has been validated.")
                finally:
                    addresses_used.append(hardware_dict["address"])

            # Check nycthemeral config
            self.logger.debug(
                f"Checking nycthemeral config for ecosystem {ecosystem_name}.")
            nycthemeral_cfg = ecosystem_cfg["environment"]["nycthemeral_cycle"]

            span_method = safe_enum_from_name(gv.NycthemeralSpanMethod, nycthemeral_cfg["span"])
            self._log_nycthemeral_method_issues(span_method, ecosystem_cfg)

            lighting_method = safe_enum_from_name(gv.LightingMethod, nycthemeral_cfg["lighting"])
            self._log_nycthemeral_method_issues(lighting_method, ecosystem_cfg)

        if unfixable_error:
            raise ConfigValidationError(
                "Could not validate ecosystems config. Check the log for more "
                "details."
            )
        return ecosystem_configs

    async def _load_ecosystems_config(self) -> None:
        # /!\ must be used with the config_files_lock acquired
        self._check_files_lock_acquired()
        # Load raw data
        config_path = self.get_file_path(ConfigType.ecosystems)
        unvalidated: dict[str, EcosystemConfigDict] = await load_yaml(config_path)
        checksum = await self._checksum_tracker.compute(config_path)
        # Validate the data structure
        try:
            validated = self._validate_ecosystem_dict(unvalidated)
        except pydantic.ValidationError as e:  # pragma: no cover
            self.logger.error(
                f"Could not load ecosystems configuration file. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`."
            )
            raise e
        # Validate the data logic
        validated = self._validate_ecosystems_logic(validated)
        # Set the ecosystems config dict
        self._ecosystems_config_dict = validated
        # Update the checksum
        await self._checksum_tracker.update(config_path, checksum)
        # Reset ecosystems caches
        for ecosystem_config in self.ecosystems_config.values():
            ecosystem_config.reset_caches()

    @staticmethod
    def _validate_private_dict(private_dict: PrivateConfigDict) -> PrivateConfigDict:
        return PrivateConfigValidator(**private_dict).model_dump()

    async def _load_private_config(self) -> None:
        # /!\ must be used with the config_files_lock acquired
        self._check_files_lock_acquired()
        # Load raw data
        config_path = self.get_file_path(ConfigType.private)
        unvalidated: PrivateConfigDict  = await load_yaml(config_path)  # ty: ignore[invalid-assignment]
        checksum = await self._checksum_tracker.compute(config_path)
        # Validate the data structure
        try:
            validated = self._validate_private_dict(unvalidated)
        except pydantic.ValidationError as e:  # pragma: no cover
            self.logger.error(
                f"Could not validate private configuration file. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`."
            )
            raise e
        # Room for possible future data logic validation
        self._private_config = validated
        # Update the checksum
        await self._checksum_tracker.update(config_path, checksum)

    async def _load_chaos_memory(self) -> None:
        self.logger.debug("Trying to load chaos memory.")
        chaos_path = self.get_file_path(CacheType.chaos)
        try:
            unvalidated = await load_json(chaos_path)
            try:
                validated: dict[str, ChaosMemory] = (
                    ChaosMemoryRootValidator
                    .model_validate(unvalidated)
                    .model_dump()
                )
            except pydantic.ValidationError:  # pragma: no cover
                self.logger.error("Error while loading chaos.")
                raise
        except (FileNotFoundError, JSONDecodeError):
            validated = {}
        incomplete = False
        for ecosystem_uid in self.ecosystems_config_dict:
            if ecosystem_uid not in validated:
                incomplete = True
                validated.update(self._create_chaos_memory(ecosystem_uid))
        self._chaos_memory = validated
        if incomplete:
            await self._dump_chaos_memory()

    async def load(self, cfg_type: ConfigType | CacheType) -> None:
        """Load config files"""
        match cfg_type:
            case ConfigType.ecosystems:
                async with self.config_files_lock:
                    self.logger.debug("Loading ecosystems configuration file.")
                    await self._load_ecosystems_config()
            case ConfigType.private:
                async with self.config_files_lock:
                    self.logger.debug("Loading private configuration file.")
                    await self._load_private_config()
            case CacheType.chaos:
                self.logger.debug("Loading chaos cache file.")
                await self._load_chaos_memory()
            case _:
                raise ValueError(f"Unknown config type: {cfg_type}")

    async def _dump_ecosystems_config(self) -> None:
        # /!\ must be used with the config_files_lock acquired
        self._check_files_lock_acquired()
        # Get the data
        cfg = deepcopy(self.ecosystems_config_dict)
        # Format it
        for uid in cfg:
            cfg[uid]["hardware"] = validate_from_root_model(
                cfg[uid]["hardware"], RootHardwareValidator, exclude_defaults=True)
            cfg[uid]["environment"]["climate"] = validate_from_root_model(
                cfg[uid]["environment"]["climate"], RootClimateValidator, exclude_defaults=True)
            cfg[uid]["plants"] = validate_from_root_model(
                cfg[uid]["plants"], RootPlantsValidator, exclude_defaults=True)
        # Dump the data
        config_path = self.get_file_path(ConfigType.ecosystems)
        await dump_yaml(cfg, config_path)
        # Update the checksum
        await self._checksum_tracker.update(config_path)

    async def _dump_private_config(self) -> None:
        # /!\ must be used with the config_files_lock acquired
        self._check_files_lock_acquired()
        # Dump the data
        config_path = self.get_file_path(ConfigType.private)
        await dump_yaml(self._private_config, config_path)  # ty: ignore[invalid-argument-type]
        # Update the checksum
        await self._checksum_tracker.update(config_path)

    async def _dump_chaos_memory(self) -> None:
        chaos_path = self.get_file_path(CacheType.chaos)
        await dump_json(self._chaos_memory, chaos_path)

    async def save(self, cfg_type: ConfigType | CacheType) -> None:
        match cfg_type:
            case ConfigType.ecosystems:
                async with self.config_files_lock:
                    self.logger.debug("Saving ecosystems configuration file.")
                    await self._dump_ecosystems_config()
            case ConfigType.private:
                async with self.config_files_lock:
                    self.logger.debug("Saving private configuration file.")
                    await self._dump_private_config()
            case CacheType.chaos:
                self.logger.debug("Saving chaos cache file.")
                await self._dump_chaos_memory()
            case _:
                raise ValueError(f"Unknown config type: {cfg_type}")

    # ---------------------------------------------------------------------------
    #   Config initialization
    # ---------------------------------------------------------------------------
    async def _create_ecosystems_config_file(self) -> None:
        self._ecosystems_config_dict = {}
        self.create_ecosystem("Default Ecosystem")
        await self._dump_ecosystems_config()

    async def _create_private_config_file(self) -> None:
        self._private_config: PrivateConfigDict = PrivateConfigValidator().model_dump()
        await self._dump_private_config()

    async def _check_hardware_requirements(self) -> None:
        # Verify that all the hardware requirements are met
        for ecosystem_cfg in self.ecosystems_config_dict.values():
            ecosystem_name: str = ecosystem_cfg["name"]
            # Check hardware config
            self.logger.debug(
                f"Checking hardware requirements for ecosystem {ecosystem_name}.")
            # Multiple hardware can have the same multiplexer. Perform the check
            #  at the end
            multiplexer_models: set[str] = set()
            for hardware_dict in ecosystem_cfg["hardware"].values():
                hardware_name: str = hardware_dict["name"]
                # The `ecosystem_configs` dict should have gone through
                #  `_validate_ecosystems_logic()` by now, so the following should
                #  never fail
                hardware_cls = Hardware.get_model_subclass(hardware_dict["model"])
                self.logger.debug(
                    f"Checking requirements for hardware {hardware_name} in ecosystem "
                    f"{ecosystem_name}.")
                try:
                    await hardware_cls.check_requirements()
                except Exception:
                    self.logger.error(
                        f"Hardware {hardware_name} in ecosystem {ecosystem_name} "
                        f"failed to meet requirements for model {hardware_cls.__name__}.")
                else:
                    self.logger.debug(
                        f"Requirements for hardware {hardware_name} in ecosystem "
                        f"{ecosystem_name} are met.")
                multiplexer_model = hardware_dict.get("multiplexer_model")
                if multiplexer_model:
                    multiplexer_models.add(multiplexer_model)
            # Check multiplexer requirements
            for multiplexer_model in multiplexer_models:
                # The `ecosystem_configs` dict should have gone through
                #  `_validate_ecosystems_logic()` by now, so the following should
                #  never fail
                multiplexer_cls = Multiplexer.get_model_subclass(multiplexer_model)
                try:
                    await multiplexer_cls.check_requirements()
                except Exception:
                    self.logger.error(
                        f"Multiplexer `{multiplexer_cls.__name__}` in ecosystem "
                        f"{ecosystem_name} failed to meet requirements")
                else:
                    self.logger.debug(
                        f"Requirements for multiplexer `{multiplexer_cls.__name__}`"
                        f" in ecosystem {ecosystem_name} are met.")


    async def initialize_configs(self) -> None:
        # This steps needs to remain separate and explicits as it loads files
        # Private configs need to be loaded first so we can check nycthemeral
        #  methods based on the private config
        # Load private config
        private_cfg_path: Path = self.get_file_path(ConfigType.private)
        if private_cfg_path.exists():
            await self.load(ConfigType.private)
        else:
            self.logger.warning(
                "No custom `private.cfg` configuration file detected. "
                "Creating a default file.")
            async with self.config_files_lock:
                await self._create_private_config_file()
        # Load ecosystems config
        ecosystems_cfg_path: Path = self.get_file_path(ConfigType.ecosystems)
        if ecosystems_cfg_path.exists():
            await self.load(ConfigType.ecosystems)
            await self._check_hardware_requirements()
        else:
            self.logger.warning(
                "No custom `ecosystems.cfg` configuration file detected. "
                "Creating a default file.")
            async with self.config_files_lock:
                await self._create_ecosystems_config_file()
        # Load chaos cache
        await self.load(CacheType.chaos)
        # Mark as loaded
        self.configs_loaded = True

    async def save_configs(self) -> None:
        await self.save(ConfigType.ecosystems)
        await self.save(ConfigType.private)
        await self.save(CacheType.chaos)

    # ---------------------------------------------------------------------------
    #   Config watchdog
    # ---------------------------------------------------------------------------
    def start_watchdog(self) -> None:
        self.watchdog.start()

    def stop_watchdog(self) -> None:
        self.watchdog.stop()

    # ---------------------------------------------------------------------------
    #   Ecosystems config interface
    # ---------------------------------------------------------------------------
    @property
    def ecosystems_config_dict(self) -> dict[str, EcosystemConfigDict]:
        return self._ecosystems_config_dict

    @property
    def ecosystems_uid(self) -> list[str]:
        return list(self.ecosystems_config_dict.keys())

    @property
    def ecosystems_name(self) -> list[str]:
        return [i["name"] for i in self.ecosystems_config_dict.values()]

    def _create_new_ecosystem_uid(self) -> str:
        used_ids = self.ecosystems_uid
        while True:
            uid = create_uid(uid_length=8)
            if uid not in used_ids:
                return uid

    def create_ecosystem(self, ecosystem_name: str) -> None:
        uid = self._create_new_ecosystem_uid()
        ecosystem_cfg = EcosystemConfigValidator(name=ecosystem_name).model_dump()
        self.ecosystems_config_dict.update({uid: ecosystem_cfg})

    def update_ecosystem_base_info(
            self,
            ecosystem_id: str,
            **updating_values: Unpack[EcosystemBaseUpdateDict],
    ) -> None:
        ecosystem_ids = self.get_IDs(ecosystem_id)
        info = remove_missing(updating_values)  # ty: ignore[invalid-argument-type]
        if "name" in info:
            self.ecosystems_config_dict[ecosystem_ids.uid]["name"] = info["name"]
        if "status" in info:
            self.ecosystems_config_dict[ecosystem_ids.uid]["status"] = info["status"]

    def delete_ecosystem(self, ecosystem_id: str) -> None:
        ecosystem_ids = self.get_IDs(ecosystem_id)
        del self.ecosystems_config_dict[ecosystem_ids.uid]

    def get_ecosystems_expected_to_run(self) -> set[str]:
        return {
            ecosystem_uid
            for ecosystem_uid, eco_cfg_dict in self.ecosystems_config_dict.items()
            if eco_cfg_dict["status"]
        }

    def get_ecosystem_name(self, ecosystem_uid: str) -> str:
        try:
            return self.ecosystems_config_dict[ecosystem_uid]["name"]
        except KeyError:
            return f"uid:{ecosystem_uid}"

    def get_IDs(self, ecosystem_id: str) -> gv.IDs:
        if ecosystem_id in self.ecosystems_uid:
            return gv.IDs(ecosystem_id, self.ecosystems_config_dict[ecosystem_id]["name"])
        for ecosystem_uid, ecosystem in self.ecosystems_config_dict.items():
            if ecosystem["name"] == ecosystem_id:
                return gv.IDs(ecosystem_uid, ecosystem["name"])
        raise EcosystemNotFound(
            f"Ecosystem with id '{ecosystem_id}' not found. 'ecosystem_id' parameter "
            f"should either be an ecosystem uid or an ecosystem name present in "
            f"the 'ecosystems.cfg' file. If you want to create a new ecosystem "
            f"configuration use the function `create_ecosystem()`."
        )

    def get_ecosystem_config(self, ecosystem_id: str) -> EcosystemConfig:
        return EcosystemConfig(ecosystem_id=ecosystem_id, engine_config=self)

    @property
    def ecosystems_config(self) -> WeakValueDictionary[str, EcosystemConfig]:
        return _MetaEcosystemConfig.instances

    # ---------------------------------------------------------------------------
    #   Private config interface
    # ---------------------------------------------------------------------------
    @property
    def private_config(self) -> PrivateConfigDict:
        return self._private_config

    @property
    def places(self) -> dict[str, gv.Coordinates]:
        return self.private_config["places"]

    def get_place(self, place: str) -> gv.Coordinates | None:
        try:
            return gv.Coordinates(*self.places[place])
        except KeyError:
            return None

    def set_place(
            self,
            place: str,
            coordinates: tuple[float, float] | CoordinatesDict,
    ) -> None:
        validated_coordinates: gv.Coordinates
        if isinstance(coordinates, tuple):
            validated_coordinates = gv.Coordinates(
                latitude=coordinates[0],
                longitude=coordinates[1]
            )
        else:
            validated_coordinates = gv.Coordinates(
                latitude=coordinates["latitude"],
                longitude=coordinates["longitude"],
            )
        self.places[place] = validated_coordinates

    def update_place(
            self,
            place: str,
            coordinates: tuple[float, float] | CoordinatesDict,
    ) -> None:
        if not self.get_place(place):
            raise UndefinedParameter(
                f"No location named '{place}' was found in the private "
                f"configuration file."
            )
        self.set_place(place, coordinates)

    def delete_place(self, place: str) -> None:
        try:
            del self.places[place]
        except KeyError:  # pragma: no cover
            raise UndefinedParameter(
                f"No location named '{place}' was found in the private "
                f"configuration file."
            )

    @property
    def home_coordinates(self) -> gv.Coordinates:
        home = self.get_place(DEFAULT_PLACE)
        if home is None:
            raise UndefinedParameter(
                "No location named 'home' was found in the private "
                "configuration file."
            )
        return home

    @home_coordinates.setter
    def home_coordinates(self, value: tuple[float, float] | CoordinatesDict) -> None:
        self.set_place(DEFAULT_PLACE, coordinates=value)

    @staticmethod
    def _is_polar_day(coord: gv.Coordinates, today: date) -> bool:
        # Range of polar day in Northern hemisphere (and polar night in Southern hemisphere)
        northern_polar_day = (
            NORTHERN_SUMMER_START_MONTH < today.month <= NORTHERN_SUMMER_END_MONTH)
        return (
            (northern_polar_day and coord.latitude > 0)
            or (not northern_polar_day and coord.latitude < 0)
        )

    def _compute_sun_times(self, place: str, coord: gv.Coordinates) -> gv.SunTimesDict:
        today = date.today()
        sun_times = get_sun_times(coord).model_dump()
        if sun_times["sunrise"] is None:  # sunset is None too
            # Handle high and low latitude specificities
            day_night = "day" if self._is_polar_day(coord, today) else "night"
            self.logger.warning(
                f"Sun times of '{place}' has no sunrise and sunset (due to "
                f"polar {day_night}). Replacing values to allow coherent "
                f"lighting."
            )
            midnight = datetime.combine(today, time(hour=0))
            msec = timedelta(milliseconds=1)
            if day_night == "day":
                sun_times["sunrise"] = midnight.time()  # Sunrise
                sun_times["sunset"] = (midnight - msec).time()  # Sunset
            else:
                sun_times["sunrise"] = midnight.time()  # Sunrise
                sun_times["sunset"] = (midnight + msec).time()  # Sunset
        return sun_times

    def get_sun_times(self, place: str) -> gv.SunTimesDict | None:
        coord = self.get_place(place)
        if coord is None:
            return None
        sun_times_cache = self._sun_times.get(place)
        today = date.today()
        if sun_times_cache is not None and sun_times_cache["last_update"] >= today:
            return sun_times_cache["data"]
        sun_times = self._compute_sun_times(place, coord)
        self._sun_times[place] = SunTimesCacheData(
            last_update=today,
            data=sun_times,
        )
        return sun_times

    @property
    def home_sun_times(self) -> gv.SunTimesDict | None:
        return self.get_sun_times(DEFAULT_PLACE)

    def refresh_sun_times(self) -> None:
        self.logger.info("Updating sun times.")
        places = set(self._sun_times.keys())
        self._sun_times.clear()
        places_ok: set[str] = set()
        places_failed: set[str] = set()
        places.update(self.places.keys())
        for place in places:
            sun_times = self.get_sun_times(place)
            if sun_times:
                places_ok.add(place)
            else:
                places_failed.add(place)
        if places_ok:
            self.logger.info(
                f"Sun times of the following targets have been refreshed: "
                f"{humanize_list(list(places_ok))}."
            )
        if places_failed:
            self.logger.warning(
                f"Failed to refresh the sun times of the following targets: "
                f"{humanize_list(list(places_failed))}. Some functionalities "
                f"might not work as expected."
            )

    @property
    def units(self) -> dict[str, str]:
        return self.private_config.get("units", {})

    # ---------------------------------------------------------------------------
    #   Chaos cache
    # ---------------------------------------------------------------------------
    @property
    def chaos_memory(self) -> dict[str, ChaosMemory]:
        return self._chaos_memory

    def _create_chaos_memory(self, ecosystem_uid: str) -> dict[str, ChaosMemory]:
        return {ecosystem_uid: ChaosMemoryValidator().model_dump()}

    def get_chaos_memory(self, ecosystem_uid: str) -> ChaosMemory:
        if ecosystem_uid not in self.ecosystems_config_dict:
            raise ValueError(
                f"No ecosystem with uid '{ecosystem_uid}' found in ecosystems "
                f"config."
            )
        if ecosystem_uid not in self._chaos_memory:
            self._chaos_memory.update(self._create_chaos_memory(ecosystem_uid))
        return self.chaos_memory[ecosystem_uid]


# ---------------------------------------------------------------------------
#   Functions to interact with the module
# ---------------------------------------------------------------------------
def get_IDs(ecosystem: str) -> gv.IDs:
    """Return the tuple (ecosystem_uid, ecosystem_name)

    :param ecosystem: str, either an ecosystem uid or ecosystem name
    """
    return EngineConfig().get_IDs(ecosystem)
