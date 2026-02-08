from __future__ import annotations

import asyncio
from asyncio import Condition, Event, Lock, Task
from contextlib import asynccontextmanager, suppress
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
import hashlib
from json.decoder import JSONDecodeError
import logging
from math import pi, sin
from pathlib import Path
import random
import typing as t
from typing import cast, Literal, Type, TypedDict, TypeVar
from weakref import WeakValueDictionary

from anyio.to_thread import run_sync
import pydantic
from pydantic import Field, field_validator, model_serializer, RootModel

import gaia_validators as gv
from gaia_validators import safe_enum_from_name
from gaia_validators.utils import get_sun_times

from gaia.config import (
    BaseConfig, configure_logging, defaults, GaiaConfig, GaiaConfigHelper)
from gaia.exceptions import (
    EcosystemNotFound, HardwareNotFound, PlantNotFound, UndefinedParameter)
from gaia.hardware import hardware_models
from gaia.subroutines import subroutine_dict
from gaia.utils import (
    create_uid, get_yaml, humanize_list, is_time_between, json, SingletonMeta)


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.engine import Engine
    from gaia.events import PayloadName


class ValidationError(ValueError):
    pass


# ---------------------------------------------------------------------------
#   Enums
# ---------------------------------------------------------------------------
class ConfigType(Enum):
    ecosystems = "ecosystems.cfg"
    private = "private.cfg"


class CacheType(Enum):
    chaos = "chaos.json"


# ---------------------------------------------------------------------------
#   Utility functions
# ---------------------------------------------------------------------------
async def _load_json(path: Path) -> dict:
    def load_json_sync() -> dict:
        with open(path, "r") as file:
            data: dict = json.loads(file.read())
        return data

    return await run_sync(load_json_sync)


async def _dump_json(data: dict, path: Path) -> None:
    def dump_json_sync() -> None:
        with open(path, "w") as file:
            file.write(json.dumps(data))

    return await run_sync(dump_json_sync)


async def _load_yaml(path: Path) -> dict:
    yaml = get_yaml()

    def load_yaml_sync() -> dict:
        with open(path, "r") as file:
            data: dict = yaml.load(file)
        return data

    return await run_sync(load_yaml_sync)


async def _dump_yaml(data: dict, path: Path) -> None:
    yaml = get_yaml()

    def dump_yaml_sync() -> None:
        with open(path, "w") as file:
            yaml.dump(data, file)

    await run_sync(dump_yaml_sync)


H = TypeVar("H", int, bytes, str)


def _file_checksum(file_path: Path, _buffer_size: int = 4096) -> H:
    try:
        with open(file_path, "rb") as file_obj:
            digest_obj = hashlib.md5(usedforsecurity=False)
            # coming from hashlib.file_digest
            buffer = bytearray(_buffer_size)
            view = memoryview(buffer)
            while True:
                size = file_obj.readinto(buffer)
                if size == 0:
                    break  # EOF
                digest_obj.update(view[:size])
            return digest_obj.digest()
    except FileNotFoundError:  # pragma: no cover
        return b"\x00"


def _to_dt(_time: time) -> datetime:
    # Transforms time to today's datetime. Needed to use timedelta
    _date = date.today()
    return datetime.combine(_date, _time)


async def event_wait(event: Event, timeout: float | int):
    # suppress TimeoutError because wait_for returns False in case of timeout
    with suppress(asyncio.TimeoutError):
        await asyncio.wait_for(event.wait(), timeout)
    return event.is_set()


# ---------------------------------------------------------------------------
#   Pydantic utility functions
# ---------------------------------------------------------------------------
def format_pydantic_error(error: pydantic.ValidationError) -> str:
    errors = error.errors()
    return ". ".join([
        f"{e['type'].replace('_', ' ').upper()} at parameter '{e['loc'][0]}', " \
        f"input '{e['input']}' is not valid."
        for e in errors
    ])


D = TypeVar("D", bound=dict)


def validate_from_root_model(
        unvalidated_data: D,
        root_model: Type[RootModel],
        *,
        exclude_defaults: bool = False,
) -> D:
    return (
        root_model
            .model_validate(unvalidated_data)
            .model_dump(exclude_defaults=exclude_defaults)
    )


# ---------------------------------------------------------------------------
#   Common config models
# ---------------------------------------------------------------------------
class SunTimesCacheData(TypedDict):
    last_update: date
    data: gv.SunTimesDict


# ---------------------------------------------------------------------------
#   Private config models
# ---------------------------------------------------------------------------
class CoordinatesDict(TypedDict):
    latitude: float
    longitude: float


class PrivateConfigValidator(gv.BaseModel):
    places: dict[str, gv.Coordinates] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)


class PrivateConfigDict(TypedDict):
    places: dict[str, gv.Coordinates]
    units: dict[str, str]


# ---------------------------------------------------------------------------
#   Ecosystem config models
# ---------------------------------------------------------------------------
class EcosystemBaseUpdateDict(TypedDict):
    name: str
    status: bool


# Custom EnvironmentConfig and EcosystemConfig models as ecosystems.cfg uses
#  uid: anonymous config dicts rather than config lists
class EnvironmentConfigValidator(gv.BaseModel):
    chaos: gv.ChaosConfig = Field(default_factory=gv.ChaosConfig)
    nycthemeral_cycle: gv.NycthemeralCycleConfig = Field(
        default_factory=gv.NycthemeralCycleConfig, validation_alias="sky")
    climate: dict[gv.ClimateParameter, gv.AnonymousClimateConfig] = \
        Field(default_factory=dict)
    weather: dict[gv.WeatherParameter, gv.AnonymousWeatherConfig] = \
        Field(default_factory=dict)


class EnvironmentConfigDict(TypedDict):
    chaos: gv.ChaosConfigDict
    nycthemeral_cycle: gv.NycthemeralCycleConfigDict
    climate: dict[gv.ClimateParameter, gv.AnonymousClimateConfigDict]
    weather: dict[gv.WeatherParameter, gv.AnonymousWeatherConfigDict]


class EcosystemConfigValidator(gv.BaseModel):
    name: str
    status: bool = False
    management: gv.ManagementConfig = Field(default_factory=gv.ManagementConfig)
    environment: EnvironmentConfigValidator = Field(
        default_factory=EnvironmentConfigValidator)
    hardware: dict[str, gv.AnonymousHardwareConfig] = Field(default_factory=dict, validation_alias="IO")
    plants: dict[str, gv.AnonymousPlantConfig] = Field(default_factory=dict)


class EcosystemConfigDict(TypedDict):
    name: str
    status: bool
    management: gv.ManagementConfigDict
    environment: EnvironmentConfigDict
    hardware: dict[str, gv.AnonymousHardwareConfigDict]
    plants: dict[str, gv.AnonymousPlantConfigDict]


class RootEcosystemsConfigValidator(RootModel):
    root: dict[str, EcosystemConfigValidator]


# Custom models to shorten climate, hardware and plants configs when dumping to YAML
class RootClimateValidator(RootModel):
    root: dict[gv.ClimateParameter, gv.AnonymousClimateConfig]


class _SerializableMeasure(gv.Measure):
    @model_serializer
    def serialize_model(self) -> str:
        return f"{self.name}|{self.unit if self.unit is not None else ''}"


class _SerializableAnonymousHardwareConfig(gv.AnonymousHardwareConfig):
    measures: list[_SerializableMeasure] = Field(default_factory=list, validation_alias="measure")


class RootHardwareValidator(RootModel):
    root: dict[str, _SerializableAnonymousHardwareConfig]


class RootPlantsValidator(RootModel):
    root: dict[str, gv.AnonymousPlantConfig]


# ---------------------------------------------------------------------------
#   Ecosystem chaos models
# ---------------------------------------------------------------------------
class ChaosMemoryValidator(gv.BaseModel):
    last_update: date = Field(default_factory=date.today)
    time_window: gv.TimeWindow = gv.TimeWindow()

    @field_validator("last_update", mode="before")
    def parse_last_update(cls, value):
        if isinstance(value, str):
            return date.fromisoformat(value)
        return value


class ChaosMemory(TypedDict):
    last_update: date
    time_window: gv.TimeWindow


class ChaosMemoryRootValidator(RootModel):
    root: dict[str, ChaosMemoryValidator]


# ---------------------------------------------------------------------------
#   EngineConfig class
# ---------------------------------------------------------------------------
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
        self._dirs: dict[str, Path] = {}
        self._engine: Engine | None = None
        self._ecosystems_config_dict: dict[str, EcosystemConfigDict] = {}
        self._private_config: PrivateConfigDict = PrivateConfigValidator().model_dump()
        self._sun_times: dict[str, SunTimesCacheData] = {}
        self._chaos_memory: dict[str, ChaosMemory] = {}
        # Watchdog threading securities
        self._config_files_checksum: dict[Path, H] = {}
        self._config_files_lock = Lock()
        self.new_config = Condition()
        self._stop_event = Event()
        self._task: Task | None = None
        self.configs_loaded: bool = False

    def __repr__(self) -> str:  # pragma: no cover
        return f"EngineConfig(watchdog={self.started})"

    @property
    def started(self) -> bool:
        return self._task is not None

    @property
    def task(self) -> Task:
        if self._task is None:
            raise AttributeError("'task' has not been set up")
        return self._task

    @task.setter
    def task(self, thread: Task | None) -> None:
        self._task = thread

    @property
    def engine(self) -> "Engine":
        if self._engine is not None:
            return self._engine
        raise AttributeError("'engine' has not been set up")

    @engine.setter
    def engine(self, value: Engine) -> None:
        self._engine = value

    @property
    def engine_set_up(self) -> bool:
        return self._engine is not None

    @property
    def app_config(self) -> GaiaConfig:
        return self._app_config

    @app_config.setter
    def app_config(self, app_config: GaiaConfig) -> None:
        if not self.app_config.TESTING:
            raise AttributeError("can't set attribute 'app_config'")
        self._app_config = app_config

    def _get_dir(self, dir_name: str) -> Path:
        try:
            return self._dirs[dir_name]
        except KeyError:
            try:
                path = Path(getattr(self.app_config, dir_name))
            except ValueError:
                raise ValueError(f"Config.{dir_name} is not a valid directory.")
            else:
                if not path.exists():
                    self.logger.warning(
                        f"'Config.{dir_name}' variable is set to a non-existing "
                        f"directory, trying to create it.")
                    path.mkdir(parents=True)
                self._dirs[dir_name] = path
                return path

    @property
    def base_dir(self) -> Path:
        return self._get_dir("DIR")

    @property
    def config_dir(self) -> Path:
        return self._get_dir("DIR")

    @property
    def logs_dir(self) -> Path:
        return self._get_dir("LOG_DIR")

    @property
    def cache_dir(self) -> Path:
        return self._get_dir("CACHE_DIR")

    def get_file_path(self, file_type: ConfigType | CacheType) -> Path:
        if isinstance(file_type, ConfigType):
            return self.config_dir / file_type.value
        if isinstance(file_type, CacheType):
            return self.cache_dir / file_type.value
        raise ValueError(f"Invalid file type: {file_type}")

    # Load and save configs and caches
    def _check_files_lock_acquired(self) -> None:
        if not self._config_files_lock.locked():
            raise RuntimeError(
                "This method must be called within a "
                "`engine_config.with config_files_lock():` block"
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
        except ValidationError as e:
            method_name = "Lighting" if method in gv.LightingMethod else "Nycthemeral span"
            ecosystem_name = ecosystem_cfg["name"]
            self.logger.warning(
                f"{method_name} method cannot be set to '{method.name}' for "
                f"ecosystem {ecosystem_name} Will fall back to 'fixed'. "
                f"ERROR msg: `{e.__class__.__name__}: {e}`"
            )

    async def _load_ecosystems_config(self) -> None:
        # /!\ must be used with the config_files_lock acquired
        self._check_files_lock_acquired()
        # Load raw data
        config_path = self.get_file_path(ConfigType.ecosystems)
        unvalidated: dict[str, EcosystemConfigDict] = await _load_yaml(config_path)
        # Validate the data structure
        try:
            validated = validate_from_root_model(unvalidated, RootEcosystemsConfigValidator)
        except pydantic.ValidationError as e:  # pragma: no cover
            self.logger.error(
                f"Could not load ecosystems configuration file. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`."
            )
            raise e

        # Validate the data logic
        unfixable_error: bool = False
        for ecosystem_uid, ecosystem_cfg in validated.items():
            ecosystem_cfg: EcosystemConfigDict
            ecosystem_name: str = ecosystem_cfg["name"]
            # Check hardware config
            self.logger.debug(
                f"Checking hardware config for ecosystem {ecosystem_name}.")
            addresses_used: list[str] = []
            for hardware_uid, hardware_dict in ecosystem_cfg["hardware"].items():
                hardware_dict: gv.HardwareConfigDict
                hardware_name: str = hardware_dict["name"]
                self.logger.debug(
                    f"Checking hardware {hardware_name} for ecosystem {ecosystem_name}.")
                try:
                    EcosystemConfig.validate_hardware_dict(
                        hardware_dict={"uid": hardware_uid, **hardware_dict},
                        addresses_used=addresses_used,
                    )
                except ValueError as e:
                    self.logger.error(
                        f"Could not validate hardware config for hardware {hardware_name} "
                        f"in ecosystem {ecosystem_name}. ERROR msg(s): `{e}`.")
                    unfixable_error = True
                else:
                    self.logger.debug(
                        f"Hardware {hardware_name} validated for ecosystem {ecosystem_name}.")
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
            raise ValidationError(
                "Could not validate ecosystems config. Check the log for more "
                "details."
            )
        # Set the ecosystems config dict
        self._ecosystems_config_dict = validated
        # Dump the config as a yaml file as it may have been updated by pydantic
        await self._dump_ecosystems_config()
        # Reset ecosystems caches
        for ecosystem_config in self.ecosystems_config.values():
            ecosystem_config.reset_caches()

    async def _load_private_config(self) -> None:
        # /!\ must be used with the config_files_lock acquired
        self._check_files_lock_acquired()
        # Load raw data
        config_path = self.get_file_path(ConfigType.private)
        unvalidated: PrivateConfigDict = await _load_yaml(config_path)
        # Validate the data structure
        try:
            validated = PrivateConfigValidator(**unvalidated).model_dump()
        except pydantic.ValidationError as e:  # pragma: no cover
            self.logger.error(
                f"Could not validate private configuration file. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`."
            )
            raise e
        # Room for possible future data logic validation
        self._private_config = validated

    async def _load_chaos_memory(self) -> None:
        self.logger.debug("Trying to load chaos memory.")
        chaos_path = self.get_file_path(CacheType.chaos)
        try:
            unvalidated = await _load_json(chaos_path)
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
                async with self.config_files_lock():
                    self.logger.debug("Loading ecosystems configuration file.")
                    await self._load_ecosystems_config()
            case ConfigType.private:
                async with self.config_files_lock():
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
        # Dump it
        config_path = self.get_file_path(ConfigType.ecosystems)
        await _dump_yaml(cfg, config_path)
        # Update the checksum
        self._config_files_checksum[config_path] = await self._file_checksum(config_path)

    async def _dump_private_config(self) -> None:
        # /!\ must be used with the config_files_lock acquired
        self._check_files_lock_acquired()
        # Dump the data
        config_path = self.get_file_path(ConfigType.private)
        await _dump_yaml(self._private_config, config_path)
        # Update the checksum
        self._config_files_checksum[config_path] = await self._file_checksum(config_path)

    async def _dump_chaos_memory(self) -> None:
        chaos_path = self.get_file_path(CacheType.chaos)
        await _dump_json(self._chaos_memory, chaos_path)

    async def save(self, cfg_type: ConfigType | CacheType) -> None:
        if self.app_config.TESTING:
            return
        match cfg_type:
            case ConfigType.ecosystems:
                async with self.config_files_lock():
                    self.logger.debug("Saving ecosystems configuration file.")
                    await self._dump_ecosystems_config()
            case ConfigType.private:
                async with self.config_files_lock():
                    self.logger.debug("Saving private configuration file.")
                    await self._dump_private_config()
            case CacheType.chaos:
                self.logger.debug("Saving chaos cache file.")
                await self._dump_chaos_memory()
            case _:
                raise ValueError(f"Unknown config type: {cfg_type}")

    # Initialize configs
    async def _create_ecosystems_config_file(self):
        self._ecosystems_config_dict = {}
        self._create_ecosystem("Default Ecosystem")
        await self._dump_ecosystems_config()

    async def _create_private_config_file(self):
        self._private_config: PrivateConfigDict = PrivateConfigValidator().model_dump()
        await self._dump_private_config()

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
            async with self.config_files_lock():
                await self._create_private_config_file()
        # Load ecosystems config
        ecosystems_cfg_path: Path = self.get_file_path(ConfigType.ecosystems)
        if ecosystems_cfg_path.exists():
            await self.load(ConfigType.ecosystems)
        else:
            self.logger.warning(
                "No custom `ecosystems.cfg` configuration file detected. "
                "Creating a default file.")
            async with self.config_files_lock():
                await self._create_ecosystems_config_file()
        # Update checksums
        self._config_files_checksum[private_cfg_path] = \
            await run_sync(_file_checksum, private_cfg_path)
        self._config_files_checksum[ecosystems_cfg_path] = \
            await run_sync(_file_checksum, ecosystems_cfg_path)
        # Load chaos cache
        await self.load(CacheType.chaos)
        # Mark as loaded
        self.configs_loaded = True

    # File watchdog
    async def _file_checksum(self, file_path: Path) -> H:
        # /!\ must be used with the config_files_lock acquired
        self._check_files_lock_acquired()
        return await run_sync(_file_checksum, file_path)

    async def _get_changed_config_files(self) -> set[ConfigType]:
        changed: set[ConfigType] = set()
        for file_path, old_checksum in self._config_files_checksum.items():
            new_checksum = await self._file_checksum(file_path)
            if new_checksum != old_checksum:
                changed.add(ConfigType(file_path.name))
        return changed

    async def _watchdog_routine(self) -> None:
        # Fill config files modification dict
        async with self.config_files_lock():
            changed_configs = await self._get_changed_config_files()
            if changed_configs:
                if ConfigType.private in changed_configs:
                    self.logger.info(
                        "Change in private configuration file detected. Updating it.")
                    await self._load_private_config()
                    cfg_path = self.get_file_path(ConfigType.private)
                    self._config_files_checksum[cfg_path] = \
                        await run_sync(_file_checksum, cfg_path)
                if ConfigType.ecosystems in changed_configs:
                    self.logger.info(
                        "Change in ecosystems configuration file detected. Updating it.")
                    await self._load_ecosystems_config()
                    cfg_path = self.get_file_path(ConfigType.ecosystems)
                    self._config_files_checksum[cfg_path] = \
                        await run_sync(_file_checksum, cfg_path)
                async with self.new_config:
                    self.new_config.notify_all()
                    # This unblocks the engine loop. It will then refresh
                    #  ecosystems, update sun times, ecosystem lighting hours
                    #  and send the data if it is connected.

    async def _watchdog_loop(self) -> None:
        # Make private config file trackable by the file watchdog
        config_path = self.get_file_path(ConfigType.private)
        if config_path not in self._config_files_checksum:
            self._config_files_checksum[config_path] = await self._file_checksum(config_path)
        # Make ecosystems config file trackable by the file watchdog
        config_path = self.get_file_path(ConfigType.ecosystems)
        if config_path not in self._config_files_checksum:
            self._config_files_checksum[config_path] = await self._file_checksum(config_path)

        # Start the actual loop
        sleep_period = self.app_config.CONFIG_WATCHER_PERIOD / 1000
        self.logger.info(
            f"Starting the configuration files watchdog loop. It will run every "
            f"{sleep_period:.3f} s.")
        while not self._stop_event.is_set():
            try:
                await self._watchdog_routine()
            except Exception as e:  # pragma: no cover
                self.logger.error(
                    f"Encountered an error while running the watchdog routine. "
                    f"ERROR msg: `{e.__class__.__name__}: {e}`."
                )
            await event_wait(self._stop_event, sleep_period)

    def start_watchdog(self) -> None:
        if not self.configs_loaded:  # pragma: no cover
            raise RuntimeError(
                "Configuration files need to be loaded in order to start "
                "the config files watchdog. To do so, use the "
                "`EngineConfig().initialize_configs()` method."
            )

        if self.started:  # pragma: no cover
            raise RuntimeError("Configuration files watchdog is already running")

        self.logger.info("Starting the configuration files watchdog.")
        self.task = asyncio.create_task(
            self._watchdog_loop(), name="config-watchdog_loop")
        self.logger.debug("Configuration files watchdog successfully started.")

    def stop_watchdog(self) -> None:
        if not self.started:  # pragma: no cover
            raise RuntimeError("Configuration files watchdog is not running")

        self.logger.info("Stopping the configuration files watchdog.")
        self._stop_event.set()
        self.task = None
        self.logger.debug("Configuration files watchdog successfully stopped.")

    @asynccontextmanager
    async def config_files_lock(self):
        """A context manager that makes sure only one process access file
        content at the time"""
        async with self._config_files_lock:
            yield

    # API
    def _create_new_ecosystem_uid(self) -> str:
        used_ids = self.ecosystems_uid
        while True:
            uid = create_uid(uid_length=8)
            if uid not in used_ids:
                return uid

    def _create_ecosystem(self, ecosystem_name: str) -> None:
        uid = self._create_new_ecosystem_uid()
        ecosystem_cfg = EcosystemConfigValidator(name=ecosystem_name).model_dump()
        self.ecosystems_config_dict.update({uid: ecosystem_cfg})

    def create_ecosystem(self, ecosystem_name: str) -> None:
        self._create_ecosystem(ecosystem_name)

    def update_ecosystem_base_info(
            self,
            ecosystem_id: str,
            **updating_values: EcosystemBaseUpdateDict,
    ) -> None:
        ecosystem_ids = self.get_IDs(ecosystem_id)
        ecosystem = self.ecosystems_config_dict.get(ecosystem_ids.uid)
        # Make extra sure no "complex" field is overridden
        updating_values.pop("management", None)
        updating_values.pop("environment", None)
        updating_values.pop("hardware", None)
        ecosystem.update(updating_values)

    def delete_ecosystem(self, ecosystem_id: str) -> None:
        ecosystem_ids = self.get_IDs(ecosystem_id)
        del self.ecosystems_config_dict[ecosystem_ids.uid]

    @property
    def ecosystems_config_dict(self) -> dict[str, EcosystemConfigDict]:
        return self._ecosystems_config_dict

    @ecosystems_config_dict.setter
    def ecosystems_config_dict(self, value: dict):
        if not self.app_config.TESTING:
            raise AttributeError("can't set attribute 'ecosystems_config_dict'")
        self._ecosystems_config_dict = value

    @property
    def private_config(self) -> PrivateConfigDict:
        return self._private_config

    @private_config.setter
    def private_config(self, value: PrivateConfigDict):
        if not self.app_config.TESTING:
            raise AttributeError("can't set attribute 'private_config'")
        self._private_config = value

    @property
    def ecosystems_uid(self) -> list[str]:
        return [i for i in self.ecosystems_config_dict.keys()]

    @property
    def ecosystems_name(self) -> list:
        return [i["name"] for i in self.ecosystems_config_dict.values()]

    @property
    def id_to_name_dict(self) -> dict:
        return {
            ecosystem_uid: eco_cfg_dict["name"]
            for ecosystem_uid, eco_cfg_dict in self.ecosystems_config_dict.items()
        }

    @property
    def name_to_id_dict(self) -> dict:
        return {
            eco_cfg_dict["name"]: ecosystem_uid
            for ecosystem_uid, eco_cfg_dict in self.ecosystems_config_dict.items()
        }

    def get_ecosystems_expected_to_run(self) -> set[str]:
        return set([
            ecosystem_uid
            for ecosystem_uid, eco_cfg_dict in self.ecosystems_config_dict.items()
            if eco_cfg_dict["status"]
        ])

    def get_ecosystem_name(self, ecosystem_uid: str) -> str:
        try:
            return self.ecosystems_config_dict[ecosystem_uid]["name"]
        except KeyError:
            return f"uid:{ecosystem_uid}"

    def get_IDs(self, ecosystem_id: str) -> gv.IDs:
        if ecosystem_id in self.ecosystems_uid:
            ecosystem_uid = ecosystem_id
            ecosystem_name = self.id_to_name_dict[ecosystem_id]
            return gv.IDs(ecosystem_uid, ecosystem_name)
        elif ecosystem_id in self.ecosystems_name:
            ecosystem_uid = self.name_to_id_dict[ecosystem_id]
            ecosystem_name = ecosystem_id
            return gv.IDs(ecosystem_uid, ecosystem_name)
        raise EcosystemNotFound(
            f"Ecosystem with id '{ecosystem_id}' not found. 'ecosystem_id' parameter "
            f"should either be an ecosystem uid or an ecosystem name present in "
            f"the 'ecosystems.cfg' file. If you want to create a new ecosystem "
            f"configuration use the function `create_ecosystem()`."
        )

    """Private config parameters"""
    @property
    def places(self) -> dict[str, gv.Coordinates]:
        return self.private_config["places"]

    def get_place(self, place: str) -> gv.Coordinates | None:
        try:
            return gv.Coordinates(*self.places[place])
        except KeyError:  # pragma: no cover
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
        home = self.get_place("home")
        if home is None:
            raise UndefinedParameter(
                "No location named 'home' was found in the private "
                "configuration file."
            )
        return home

    @home_coordinates.setter
    def home_coordinates(self, value: tuple[float, float] | CoordinatesDict) -> None:
        self.set_place("home", coordinates=value)

    @property
    def units(self) -> dict[str, str]:
        return self.private_config.get("units", {})

    @property
    def sun_times(self) -> dict[str, SunTimesCacheData]:
        return self._sun_times

    @sun_times.setter
    def sun_times(self, sun_times: dict[str, SunTimesCacheData]) -> None:
        if not self.app_config.TESTING:
            raise AttributeError("can't set attribute 'sun_times'")
        self._sun_times = sun_times

    def get_sun_times(self, place: str) -> gv.SunTimesDict | None:
        coord = self.get_place(place)
        if coord is None:
            return None
        sun_times = self.sun_times.get(place)
        today = date.today()
        if sun_times is None or sun_times["last_update"] < today:
            new_sun_times = get_sun_times(coord).model_dump()
            # Handle high and low latitude specificities
            if (
                # Range of polar day in Northern hemisphere
                3 < today.month <= 9
                and coord.latitude > 0
                # Range of polar day in Southern hemisphere
                or not (3 < today.month <= 9)
                and coord.latitude < 0
            ):
                day_night = "day"
            else:
                day_night = "night"
            if new_sun_times["sunrise"] is None:  # sunset is None too
                self.logger.warning(
                    f"Sun times of '{place}' has no sunrise and sunset (due to "
                    f"polar {day_night}). Replacing values to allow coherent "
                    f"lighting."
                )
                midnight = datetime.combine(today, time(hour=0))
                msec = timedelta(milliseconds=1)
                if day_night == "day":
                    new_sun_times["sunrise"] = midnight.time()          # Sunrise
                    new_sun_times["sunset"] = (midnight - msec).time()  # Sunset
                else:
                    new_sun_times["sunrise"] = midnight.time()          # Sunrise
                    new_sun_times["sunset"] = (midnight + msec).time()  # Sunset
            self.set_sun_times(place, new_sun_times)
        return self.sun_times[place]["data"]

    def set_sun_times(self, place: str, sun_times: gv.SunTimesDict) -> None:
        validated_sun_times: gv.SunTimesDict = gv.SunTimes(**sun_times).model_dump()
        self._sun_times[place] = SunTimesCacheData(
            last_update=date.today(),
            data=validated_sun_times,
        )

    @property
    def home_sun_times(self) -> gv.SunTimesDict | None:
        return self.get_sun_times("home")

    def refresh_sun_times(self) -> None:
        self.logger.info("Updating sun times.")
        places_ok: set[str] = set()
        places_failed: set[str] = set()
        for place in self.places.keys():
            ok = self.get_sun_times(place)
            if ok:
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
    def chaos_memory(self) -> dict[str, ChaosMemory]:
        return self._chaos_memory

    @chaos_memory.setter
    def chaos_memory(self, chaos_memory: dict[str, ChaosMemory]) -> None:
        if not self.app_config.TESTING:
            raise AttributeError("can't set attribute 'chaos_memory'")
        self._chaos_memory = chaos_memory

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

    def get_ecosystem_config(self, ecosystem_id: str) -> "EcosystemConfig":
        return EcosystemConfig(ecosystem_id=ecosystem_id, engine_config=self)

    @property
    def ecosystems_config(self) -> dict[str, EcosystemConfig]:
        return _MetaEcosystemConfig.instances


# ---------------------------------------------------------------------------
#   EcosystemConfig class
# ---------------------------------------------------------------------------
class _MetaEcosystemConfig(type):
    instances: dict[str, "EcosystemConfig"] = WeakValueDictionary()

    def __call__(cls, *args, **kwargs) -> "EcosystemConfig":
        try:
            ecosystem_id = kwargs["ecosystem_id"]
        except KeyError:
            try:
                ecosystem_id = args[0]
            except IndexError:  # pragma: no cover
                raise TypeError(
                    "EcosystemConfig() missing 1 required argument: 'ecosystem_id'"
                )
        engine_config = EngineConfig()
        if not engine_config.configs_loaded:
            raise RuntimeError(
                "Configuration files need to be loaded by `EngineConfig` in "
                "order to get an `EcosystemConfig` instance. To do so, use the "
                "`EngineConfig().initialize_configs()` method."
            )
        ecosystem_uid = engine_config.get_IDs(ecosystem_id).uid
        try:
            return cls.instances[ecosystem_uid]
        except KeyError:
            ecosystem_config: EcosystemConfig = \
                cls.__new__(cls, ecosystem_uid, *args, **kwargs)
            ecosystem_config.__init__(*args, **kwargs)
            cls.instances[ecosystem_uid] = ecosystem_config
            return ecosystem_config


class EcosystemConfig(metaclass=_MetaEcosystemConfig):
    def __init__(
            self,
            ecosystem_id: str,
            engine_config: EngineConfig | None = None,
    ) -> None:
        engine_config = engine_config or EngineConfig()
        self._engine_config: EngineConfig = engine_config
        ids = self._engine_config.get_IDs(ecosystem_id)
        self.uid = ids.uid
        name = ids.name.replace(" ", "_")
        self.logger = logging.getLogger(f"gaia.engine.{name}.config")
        self._nycthemeral_span_method: gv.NycthemeralSpanMethod | None = None
        self._nycthemeral_span_hours: gv.NycthemeralSpanConfig | None = None
        self._lighting_method: gv.LightingMethod | None = None
        self._lighting_hours: gv.LightingHours | None = None

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"{self.__class__.__name__}({self.uid}, name={self.name}, "
            f"engine_config={self._engine_config})"
        )

    # ---------------------------------------------------------------------------
    #   Properties and common utilities
    # ---------------------------------------------------------------------------
    @property
    def _config_dict(self) -> EcosystemConfigDict:
        return self._engine_config.ecosystems_config_dict[self.uid]

    @property
    def general(self) -> EngineConfig:
        return self._engine_config

    @property
    def name(self) -> str:
        return self._config_dict["name"]

    @name.setter
    def name(self, value: str) -> None:
        self._config_dict["name"] = value

    @property
    def status(self) -> bool:
        return self._config_dict["status"]

    @status.setter
    def status(self, value: bool) -> None:
        self._config_dict["status"] = value

    async def save(self) -> None:
        """Persist the ecosystem configuration to the ecosystems.cfg file."""
        await self._engine_config.save(ConfigType.ecosystems)

    def reset_nycthemeral_caches(self) -> None:
        """Clear cached nycthemeral span and lighting values.

        Forces recomputation on next access.
        """
        self._nycthemeral_span_method = None
        self._nycthemeral_span_hours = None
        self._lighting_method = None
        self._lighting_hours = None

    def reset_caches(self) -> None:
        """Clear all cached configuration values."""
        self.reset_nycthemeral_caches()

    async def _send_payload_if_possible(self, payload_type: PayloadName) -> None:
        """Send payload if engine is connected, logging any errors."""
        if not (self.general.engine_set_up and self.general.engine.message_broker_started):
            return
        try:
            await self.general.engine.event_handler.send_payload_if_connected(
                payload_type, ecosystem_uids=[self.uid])
        except Exception as e:
            self.logger.error(
                f"Encountered an error while sending {payload_type}. "
                f"ERROR msg: `{e.__class__.__name__}: {e}`"
            )

    # ---------------------------------------------------------------------------
    #   Ecosystem management (subroutine and other capabilities)
    # ---------------------------------------------------------------------------
    @property
    def managements(self) -> gv.ManagementConfigDict:
        return self._config_dict["management"]

    @managements.setter
    def managements(self, value: gv.ManagementConfigDict) -> None:
        self._config_dict["management"] = gv.ManagementConfig(**value).model_dump()

    @property
    def management_flag(self) -> int:
        management_config = gv.ManagementConfig(**self.managements)
        return management_config.to_flag()

    def get_management(
            self,
            management: str | gv.ManagementFlags,
    ) -> bool:
        """Check if a management capability is enabled.

        :param management: The management flag name or enum to check.
        :return: True if the management capability is enabled.
        """
        validated_management = safe_enum_from_name(gv.ManagementFlags, management)
        # If management has dependencies, load them
        if validated_management >= 256:
            base_name: str = validated_management.name
            dep = f"{validated_management.name}_enabled"
            validated_management = safe_enum_from_name(gv.ManagementFlags, dep)
            self.logger.debug(
                f"{base_name.upper()} management has dependencies. Checking for "
                f"{validated_management.name} ({validated_management.value}).")
        flag = self.management_flag
        return flag & validated_management == validated_management

    def set_management(
            self,
            management: str | gv.ManagementFlags,
            value: bool,
    ) -> None:
        """Enable or disable a management capability.

        :param management: The management flag name or enum to set.
        :param value: True to enable, False to disable.
        """
        validated_management = safe_enum_from_name(gv.ManagementFlags, management)
        management_name: str = validated_management.name
        if validated_management >= 256 and value:
            composite_name = f"{validated_management.name}_enabled"
            composite_mgmt = safe_enum_from_name(gv.ManagementFlags, composite_name)
            flag = self.management_flag
            if not flag & composite_mgmt == composite_mgmt:
                dep = gv.ManagementFlags(composite_mgmt - validated_management)
                self.logger.warning(
                    f"{management_name.upper()} management has unmet dependencies: "
                    f"{dep}. This might lead to issues if it is not enabled.")
        self._config_dict["management"][management_name] = value

    def get_subroutines_enabled(self) -> list[str]:
        """Return the list of subroutine names that are enabled for this ecosystem."""
        return [
            subroutine
            for subroutine in subroutine_dict
            if self.get_management(subroutine)
        ]

    # ---------------------------------------------------------------------------
    #   Environment parameters
    # ---------------------------------------------------------------------------
    @property
    def environment(self) -> EnvironmentConfigDict:
        """
        Returns the environment config for the ecosystem
        """
        try:
            return self._config_dict["environment"]
        except KeyError:  # pragma: no cover
            self._config_dict["environment"] = EnvironmentConfigValidator().model_dump()
            return self._config_dict["environment"]

    # ---------------------------------------------------------------------------
    #      Nycthemeral cycle parameters
    # ---------------------------------------------------------------------------
    @property
    def nycthemeral_cycle(self) -> gv.NycthemeralCycleConfigDict:
        """
        Returns the nycthemeral cycle config for the ecosystem
        """
        try:
            return self.environment["nycthemeral_cycle"]
        except KeyError:  # pragma: no cover
            self.environment["nycthemeral_cycle"] = \
                gv.NycthemeralCycleConfig().model_dump()
            return self.environment["nycthemeral_cycle"]

    async def set_nycthemeral_cycle(
            self,
            **value: gv.NycthemeralCycleConfigDict,
    ) -> None:
        """Set all nycthemeral cycle parameters at once.

        :param value: Nycthemeral cycle configuration with target, span, day,
                      night, and lighting keys.
        :raises ValueError: If the provided parameters are invalid.
        """
        try:
            validated_value = gv.NycthemeralCycleConfig(**value).model_dump()
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid time parameters provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`."
            )
        await self.set_nycthemeral_span_target(validated_value["target"], False)
        await self.set_nycthemeral_span_method(validated_value["span"], False)
        await self.set_nycthemeral_span_hours({
            "day": validated_value["day"], "night": validated_value["night"]}, False)
        await self.set_lighting_method(validated_value["lighting"], False)
        # self.reset_nycthemeral_caches()  # Done in refresh_lighting_hours()
        await self.refresh_lighting_hours(send_info=True)

    @property
    def nycthemeral_span_target(self) -> str | None:
        return self.nycthemeral_cycle["target"]

    async def set_nycthemeral_span_target(
            self,
            target: str | None,
            send_info: bool = False,
    ) -> None:
        """Set the target location for nycthemeral span calculations.

        :param target: Name of a place defined in private config, or None.
        :param send_info: Whether to send updated info to connected clients.
        :raises ValueError: If the target place is not defined.
        """
        if target is not None:
            place = self.general.get_place(target)
            if place is None:
                raise ValueError(
                    "The place targeted must first be set with "
                    "`EngineConfig.set_place` before using it as a target."
                )
        self.nycthemeral_cycle["target"] = target
        self.reset_nycthemeral_caches()
        await self.refresh_lighting_hours(send_info=send_info)

    @staticmethod
    def validate_nycthemeral_method(
            method: gv.LightingMethod | gv.NycthemeralSpanMethod,
            ecosystem_dict: EcosystemConfigDict,
            places_dict: dict[str, gv.Coordinates],
    ) -> None:
        """Validate that a nycthemeral or lighting method can be used.

        :param method: The lighting or nycthemeral span method to validate.
        :param ecosystem_dict: The ecosystem configuration dictionary.
        :param places_dict: Dictionary of available places with coordinates.
        :raises ValidationError: If the method requires a target that is not
                                 available or configured.
        """
        if method == 0:  # Fixed, no target needed
            return
        # Try to get the target
        target: str
        if (method & gv.LightingMethod.elongate) == gv.LightingMethod.elongate:
            target = "home"
        elif (method & gv.NycthemeralSpanMethod.mimic) == gv.NycthemeralSpanMethod.mimic:
            nyct_cfg: gv.NycthemeralCycleConfigDict = \
                ecosystem_dict["environment"]["nycthemeral_cycle"]
            target = nyct_cfg.get("target")
            if target is None:
                raise ValidationError(
                    "Nycthemeral span method method cannot be 'mimic' as no "
                    "target is specified in the ecosystems configuration file."
                )
        else:  # pragma: no cover
            raise ValidationError(
                "'method' should be either a valid lighting method or a valid "
                "nycthemeral span method."
            )

        m = "Lighting" if isinstance(method, gv.LightingMethod) else "Nycthemeral span"
        # Verify we have the target's coordinates
        place = places_dict.get(target, None)
        if place is None:
            raise ValidationError(
                f"{m} method cannot be '{method.name}' as the coordinates of "
                f"'{target}' are not provided in the private configuration file."
            )
        # Assume the sun times can be computed

    def _log_nycthemeral_method_issues(
            self,
            method: gv.LightingMethod | gv.NycthemeralSpanMethod,
    ) -> None:
        """Validate method and log warning if it will fall back to 'fixed'."""
        try:
            self.validate_nycthemeral_method(
                method, self._config_dict, self.general.private_config["places"])
        except ValidationError as e:
            method_name = "Lighting" if method in gv.LightingMethod else "Nycthemeral span"
            self.logger.warning(
                f"{method_name} method cannot be set to '{method.name}'. Will "
                f"fall back to 'fixed'. ERROR msg: `{e.__class__.__name__}: {e}`"
            )

    def _compute_nycthemeral_span_method(self) -> gv.NycthemeralSpanMethod:
        span_method: gv.NycthemeralSpanMethod = safe_enum_from_name(
            gv.NycthemeralSpanMethod, self.nycthemeral_cycle["span"])
        # Log any incompatibilities with the nycthemeral span method chosen
        self._log_nycthemeral_method_issues(span_method)
        # If using fixed method, no check required
        if span_method & gv.NycthemeralSpanMethod.fixed:
            return gv.NycthemeralSpanMethod.fixed
        # Else, we need to make sure we have suntimes for the nycthemeral target
        target = self.nycthemeral_span_target
        sun_times = self.general.get_sun_times(target)
        if sun_times is None:
            return gv.NycthemeralSpanMethod.fixed
        else:
            return gv.NycthemeralSpanMethod.mimic

    @property
    def nycthemeral_span_method(self) -> gv.NycthemeralSpanMethod:
        if self._nycthemeral_span_method is None:
            self._nycthemeral_span_method = self._compute_nycthemeral_span_method()
        return self._nycthemeral_span_method

    async def set_nycthemeral_span_method(
            self,
            method: gv.NycthemeralSpanMethod,
            send_info: bool = True,
    ) -> None:
        """Set the method for determining nycthemeral span (day/night periods).

        :param method: Either 'fixed' or 'mimic' to follow a target location.
        :param send_info: Whether to send updated info to connected clients.
        :raises ValidationError: If the method requires unavailable configuration.
        """
        method = safe_enum_from_name(gv.NycthemeralSpanMethod, method)
        self.validate_nycthemeral_method(
            method, self._config_dict, self.general.private_config["places"])
        self.nycthemeral_cycle["span"] = method
        # self.reset_nycthemeral_caches()  # Done in refresh_lighting_hours()
        await self.refresh_lighting_hours(send_info=send_info)

    def _compute_nycthemeral_span_hours(self) -> gv.NycthemeralSpanConfig:
        if self.nycthemeral_span_method == gv.NycthemeralSpanMethod.mimic:
            target = self.nycthemeral_span_target
            sun_times = self.general.get_sun_times(target)
            if sun_times is not None:
                assert sun_times["sunrise"] is not None
                assert sun_times["sunset"] is not None
                return gv.NycthemeralSpanConfig(
                    day=sun_times["sunrise"],
                    night=sun_times["sunset"],
                )
        return gv.NycthemeralSpanConfig(
            day=self.nycthemeral_cycle["day"],
            night=self.nycthemeral_cycle["night"],
        )

    @property
    def nycthemeral_span_hours(self) -> gv.NycthemeralSpanConfig:
        if self._nycthemeral_span_hours is None:
            self._nycthemeral_span_hours = self._compute_nycthemeral_span_hours()
        return self._nycthemeral_span_hours

    async def set_nycthemeral_span_hours(
            self,
            value: gv.NycthemeralSpanConfigDict,
            send_info: bool = True,
    ) -> None:
        """Set time parameters

        :param value: A dict in the form {'day': '8h00', 'night': '22h00'}
        :param send_info: A boolean indicating whether to send a "light_data" payload
        """
        try:
            validated_value = gv.NycthemeralSpanConfig(**value).model_dump()
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid time parameters provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`."
            )
        self.environment["nycthemeral_cycle"].update(validated_value)
        # self.reset_nycthemeral_caches()  # Done in refresh_lighting_hours()
        await self.refresh_lighting_hours(send_info=send_info)

    @property
    def period_of_day(self) -> gv.PeriodOfDay:
        nycthemeral_span = self.nycthemeral_span_hours
        if is_time_between(
                nycthemeral_span.day,
                nycthemeral_span.night,
                datetime.now().time(),
        ):
            return gv.PeriodOfDay.day
        return gv.PeriodOfDay.night

    def _compute_lighting_method(self) -> gv.LightingMethod:
        lighting_method: gv.LightingMethod = safe_enum_from_name(
            gv.LightingMethod, self.nycthemeral_cycle["lighting"])
        # During testing, we just accept any lighting method
        if self.general.app_config.TESTING:
            return lighting_method
        # Log any incompatibilities with the lighting method chosen
        self._log_nycthemeral_method_issues(lighting_method)
        # If using fixed method, no check is required
        if lighting_method & gv.LightingMethod.fixed:
            return gv.LightingMethod.fixed
        # Otherwise, we need to make sure we have suntimes for "home"
        sun_times = self.general.get_sun_times("home")
        if sun_times is None:
            return gv.LightingMethod.fixed
        else:
            return gv.LightingMethod.elongate

    @property
    def lighting_method(self) -> gv.LightingMethod:
        if self._lighting_method is None:
            self._lighting_method = self._compute_lighting_method()
        return self._lighting_method

    async def set_lighting_method(
            self,
            method: gv.LightingMethod,
            send_info: bool = True,
    ) -> None:
        method = safe_enum_from_name(gv.LightingMethod, method)
        self.validate_nycthemeral_method(
            method, self._config_dict, self.general.private_config["places"])
        self.nycthemeral_cycle["lighting"] = method
        # self.reset_nycthemeral_caches()  # Done in refresh_lighting_hours()
        await self.refresh_lighting_hours(send_info=send_info)

    def _compute_lighting_hours(self) -> gv.LightingHours:
        # Start by getting morning_start and evening_end
        nycthemeral_span: gv.NycthemeralSpanConfig = self.nycthemeral_span_hours
        morning_start: time = nycthemeral_span.day
        evening_end: time = nycthemeral_span.night
        # Then fill in morning_end and evening_start
        morning_end: time
        evening_start: time
        lighting_method: gv.LightingMethod = self.lighting_method

        # Computation for 'fixed' lighting method
        if lighting_method == gv.LightingMethod.fixed:
            dt_morning_start: datetime = _to_dt(morning_start)
            day_span: timedelta = _to_dt(evening_end) - dt_morning_start
            half_day: datetime = dt_morning_start + (day_span / 2)
            morning_end = (half_day - timedelta(milliseconds=1)).time()
            evening_start = half_day.time()

        # Computation for 'elongate' lighting method
        elif lighting_method == gv.LightingMethod.elongate:
            home_sun_times = self.general.home_sun_times
            sunrise: datetime = _to_dt(home_sun_times["sunrise"])
            sunset: datetime = _to_dt(home_sun_times["sunset"])
            # Civil dawn can be None for high latitude at dates close to solstices.
            # In this case, use an offset of 1h30
            civil_dawn_time: time | None = home_sun_times["civil_dawn"]
            offset: timedelta
            if civil_dawn_time is None:
                offset = timedelta(hours=1, minutes=30)
            else:
                civil_dawn: datetime = _to_dt(civil_dawn_time)
                offset = sunrise - civil_dawn
            morning_end = (sunrise + offset).time()
            evening_start = (sunset - offset).time()
        else:  # Should not be possible
            raise ValueError

        return gv.LightingHours(
            morning_start=morning_start,
            # Morning should not end later than evening
            morning_end=min(morning_end, evening_end),
            # Evening should not start before morning
            evening_start=max(evening_start, morning_start),
            evening_end=evening_end,
        )

    @property
    def lighting_hours(self) -> gv.LightingHours:
        if self._lighting_hours is None:
            self._lighting_hours = self._compute_lighting_hours()
        return self._lighting_hours

    @lighting_hours.setter
    def lighting_hours(self, lighting_hours: gv.LightingHours) -> None:
        if not self.general.app_config.TESTING:
            raise AttributeError(
                "'lighting_hours' can only be set when 'TESTING' is True.")
        self._lighting_hours = lighting_hours
        # DO NOT USE THIS as it will overwrite the newly set value
        # self.reset_nycthemeral_caches()

    async def refresh_lighting_hours(self, send_info: bool = True) -> None:
        self.logger.info("Refreshing lighting hours.")

        # Reset caches ...
        self.reset_nycthemeral_caches()

        # ... compute the span method and hours ...
        self._nycthemeral_span_method = self._compute_nycthemeral_span_method()
        self._nycthemeral_span_hours = self._compute_nycthemeral_span_hours()

        # ... and the lighting method and hours
        self._lighting_method = self._compute_lighting_method()
        self._lighting_hours = self._compute_lighting_hours()

        if send_info:
            await self._send_payload_if_possible("nycthemeral_info")

    # ---------------------------------------------------------------------------
    #      Chaos parameters
    # ---------------------------------------------------------------------------
    @property
    def chaos_config(self) -> gv.ChaosConfig:
        return gv.ChaosConfig(**self.environment["chaos"])

    @chaos_config.setter
    def chaos_config(self, values: gv.ChaosConfigDict) -> None:
        """Set chaos parameter

        :param values: A dict with the entries 'frequency': int,
                       'duration': int and 'intensity': float.
        """
        try:
            validated_values = gv.ChaosConfig(**values).model_dump()
        except pydantic.ValidationError as e:  # pragma: no cover
            raise ValueError(
                f"Invalid chaos parameters provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`."
            )
        self.environment["chaos"] = validated_values

    @property
    def chaos_time_window(self) -> gv.TimeWindow:
        #chaos_memory = self.general.get_chaos_memory(self.uid)
        #if chaos_memory["last_update"] < date.today():
        #    await self._update_chaos_time_window()
        return self.general.get_chaos_memory(self.uid)["time_window"]

    async def update_chaos_time_window(self, send_info: bool = True) -> None:
        """Update the chaos time window if it hasn't been updated today.

        Randomly determines whether a chaos period should begin based on the
        configured frequency.

        :param send_info: Whether to send chaos_parameters payload to clients.
        """
        self.logger.info("Updating chaos time window.")
        if self.general.get_chaos_memory(self.uid)["last_update"] < date.today():
            await self._update_chaos_time_window()
            if send_info:
                await self._send_payload_if_possible("chaos_parameters")
        else:
            self.logger.debug("Chaos time window is already up to date.")

    async def _update_chaos_time_window(self) -> None:
        chaos_memory = self.general.get_chaos_memory(self.uid)
        beginning = chaos_memory["time_window"]["beginning"]
        end = chaos_memory["time_window"]["end"]
        if beginning and end:
            if not (beginning <= datetime.now(timezone.utc) <= end):  # End of chaos period
                beginning = None
                end = None
        else:
            if self.chaos_config.frequency:
                chaos_probability = random.randint(1, self.chaos_config.frequency)
            else:
                chaos_probability = 0
            if chaos_probability == 1:
                today = datetime.now(timezone.utc).replace(
                    hour=14, minute=0, second=0, microsecond=0)
                beginning = today
                end = today + timedelta(days=self.chaos_config.duration)
        chaos_memory["time_window"]["beginning"] = beginning
        chaos_memory["time_window"]["end"] = end
        chaos_memory["last_update"] = date.today()
        await self.general.save(CacheType.chaos)

    def get_chaos_factor(self, now: datetime | None = None) -> float:
        """Calculate the current chaos factor based on time within chaos window.

        Returns a sinusoidal factor that peaks at the middle of the chaos period.

        :param now: The current time, defaults to now in UTC.
        :return: A float from 1.0 (no chaos) to chaos intensity (peak chaos).
        """
        beginning = self.chaos_time_window["beginning"]
        end = self.chaos_time_window["end"]
        if beginning is None or end is None:
            return 1.0
        now = now or datetime.now(timezone.utc)
        chaos_duration = (end - beginning).total_seconds() // 60
        chaos_start_to_now = (now - beginning).total_seconds() // 60
        chaos_fraction = chaos_start_to_now / chaos_duration
        chaos_radian = chaos_fraction * pi
        return (sin(chaos_radian) * (self.chaos_config.intensity - 1.0)) + 1.0

    @property
    def chaos_parameters(self) -> gv.ChaosParameters:
        return gv.ChaosParameters(**{
            **self.environment["chaos"],
            "time_window": self.chaos_time_window,
        })

    # ---------------------------------------------------------------------------
    #      Climate parameters
    # ---------------------------------------------------------------------------
    @property
    def climate(self) -> dict[gv.ClimateParameter, gv.AnonymousClimateConfigDict]:
        """
        Returns the climate config for the ecosystem
        """
        try:
            return self.environment["climate"]
        except KeyError:  # pragma: no cover
            self.environment["climate"] = {}
            return self.environment["climate"]

    def has_climate_parameter(self, parameter: str | gv.ClimateParameter) -> bool:
        """Check if a climate parameter is configured for this ecosystem."""
        parameter = safe_enum_from_name(gv.ClimateParameter, parameter)
        return parameter in self.climate

    def get_climate_parameter(
            self,
            parameter: str | gv.ClimateParameter,
    ) -> gv.ClimateConfig:
        """Get the configuration for a specific climate parameter.

        :param parameter: The climate parameter name or enum.
        :return: The climate configuration for the parameter.
        :raises UndefinedParameter: If the parameter is not configured.
        """
        parameter = safe_enum_from_name(gv.ClimateParameter, parameter)
        try:
            data = self.climate[parameter]
        except KeyError:
            raise UndefinedParameter(
                f"No climate parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file."
            )
        else:
            return gv.ClimateConfig(parameter=parameter, **data)

    def set_climate_parameter(
            self,
            parameter: str | gv.ClimateParameter,
            **value: gv.AnonymousClimateConfigDict,
    ) -> None:
        """Set or create a climate parameter configuration.

        :param parameter: The climate parameter name or enum.
        :param value: The climate configuration values.
        :raises ValueError: If the provided values are invalid.
        """
        parameter = safe_enum_from_name(gv.ClimateParameter, parameter)
        try:
            validated_value = gv.AnonymousClimateConfig(**value).model_dump()
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid climate parameters provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`."
            )
        self.climate[parameter] = validated_value

    def update_climate_parameter(
            self,
            parameter: str | gv.ClimateParameter,
            **value: gv.AnonymousClimateConfigDict,
    ) -> None:
        """Update an existing climate parameter configuration.

        :param parameter: The climate parameter name or enum.
        :param value: The climate configuration values to update.
        :raises UndefinedParameter: If the parameter does not exist.
        """
        parameter = safe_enum_from_name(gv.ClimateParameter, parameter)
        if not self.climate.get(parameter):
            raise UndefinedParameter(
                f"No climate parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file."
            )
        self.set_climate_parameter(parameter, **value)

    def delete_climate_parameter(
            self,
            parameter: str | gv.ClimateParameter,
    ) -> None:
        """Delete a climate parameter from the configuration.

        :param parameter: The climate parameter name or enum.
        :raises UndefinedParameter: If the parameter does not exist.
        """
        parameter = safe_enum_from_name(gv.ClimateParameter, parameter)
        try:
            del self.climate[parameter]
        except KeyError:
            raise UndefinedParameter(
                f"No climate parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file")

    # ---------------------------------------------------------------------------
    #      Weather parameters
    # ---------------------------------------------------------------------------
    @property
    def weather(self) -> dict[gv.WeatherParameter, gv.AnonymousWeatherConfigDict]:
        """
        Returns the weather config for the ecosystem
        """
        return self.environment["weather"]

    def has_weather_parameter(self, parameter: str | gv.WeatherParameter) -> bool:
        """Check if a weather parameter is configured for this ecosystem."""
        parameter = safe_enum_from_name(gv.WeatherParameter, parameter)
        return parameter in self.weather

    def get_weather_parameter(self, parameter: str | gv.WeatherParameter) -> gv.WeatherConfig:
        """Get the configuration for a specific weather parameter.

        :param parameter: The weather parameter name or enum.
        :return: The weather configuration for the parameter.
        :raises UndefinedParameter: If the parameter is not configured.
        """
        parameter = safe_enum_from_name(gv.WeatherParameter, parameter)
        try:
            data = self.weather[parameter]
        except KeyError:
            raise UndefinedParameter(
                f"No weather parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file."
            )
        else:
            return gv.WeatherConfig(parameter=parameter, **data)

    def set_weather_parameter(
            self,
            parameter: str | gv.WeatherParameter,
            **value: gv.AnonymousWeatherConfigDict,
    ) -> None:
        """Set or create a weather parameter configuration.

        :param parameter: The weather parameter name or enum.
        :param value: The weather configuration values.
        :raises ValueError: If the provided values are invalid.
        """
        parameter = safe_enum_from_name(gv.WeatherParameter, parameter)
        try:
            validated_value = gv.AnonymousWeatherConfig(**value).model_dump()
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid weather config provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`."
            )
        self.weather[parameter] = validated_value

    def update_weather_parameter(
            self,
            parameter: str | gv.WeatherParameter,
            **value: gv.AnonymousWeatherConfigDict,
    ) -> None:
        """Update an existing weather parameter configuration.

        :param parameter: The weather parameter name or enum.
        :param value: The weather configuration values to update.
        :raises UndefinedParameter: If the parameter does not exist.
        """
        parameter = safe_enum_from_name(gv.WeatherParameter, parameter)
        if not self.weather.get(parameter):
            raise UndefinedParameter(
                f"No weather parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file."
            )
        self.set_weather_parameter(parameter, **value)

    def delete_weather_parameter(
            self,
            parameter: str | gv.WeatherParameter,
    ) -> None:
        """Delete a weather parameter from the configuration.

        :param parameter: The weather parameter name or enum.
        :raises UndefinedParameter: If the parameter does not exist.
        """
        parameter = safe_enum_from_name(gv.WeatherParameter, parameter)
        try:
            del self.weather[parameter]
        except KeyError:
            raise UndefinedParameter(
                f"No weather parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file"
            )

    # ---------------------------------------------------------------------------
    #   Actuator couples
    # ---------------------------------------------------------------------------
    def get_climate_actuators(self) -> dict[gv.ClimateParameter, gv.ActuatorCouple]:
        """Get actuator couples for all climate parameters.

        Merges default actuator couples with those defined in climate config.
        """
        return {
            **defaults.actuator_couples,
            **{
                climate_parameter: gv.ActuatorCouple(
                    increase=climate_cfg["linked_actuators"]["increase"],
                    decrease=climate_cfg["linked_actuators"]["decrease"],
                )
                for climate_parameter, climate_cfg in self.climate.items()
                if climate_cfg["linked_actuators"] is not None
            }
        }

    def get_weather_actuators(self) -> dict[gv.WeatherParameter, gv.ActuatorCouple]:
        """Get actuator couples for all weather parameters."""
        return {
            weather_parameter: gv.ActuatorCouple(
                increase=weather_cfg["linked_actuator"] \
                    if weather_cfg["linked_actuator"] \
                    else weather_parameter,
                decrease=None,
            )
            for weather_parameter, weather_cfg in self.weather.items()
        }

    def get_actuator_couples(self) -> dict[gv.ClimateParameter, gv.ActuatorCouple]:
        """Get all actuator couples (climate and weather combined)."""
        return self.get_climate_actuators() | self.get_weather_actuators()

    def get_actuator_to_parameter(self) -> dict[str, gv.ClimateParameter]:
        """Get a mapping from actuator group names to their parameters."""
        return defaults.get_actuator_to_parameter(self.get_actuator_couples())

    def get_actuator_to_direction(self) -> dict[str, Literal["increase", "decrease"]]:
        """Get a mapping from actuator group names to their direction."""
        return defaults.get_actuator_to_direction(self.get_actuator_couples())

    def get_valid_actuator_groups(self) -> set[str]:
        """Get the set of valid actuator group names for this ecosystem."""
        return {
            actuator_group
            for actuator_group in self.get_actuator_to_parameter().keys()
        }

    # ---------------------------------------------------------------------------
    #   Hardware parameters
    # ---------------------------------------------------------------------------
    @property
    def hardware_dict(self) -> dict[str, gv.AnonymousHardwareConfigDict]:
        """
        Returns the hardware present in the ecosystem
        """
        try:
            return self._config_dict["hardware"]
        except KeyError:  # pragma: no cover
            self._config_dict["hardware"] = {}
            return self._config_dict["hardware"]

    def get_hardware_group_uids(
        self,
        hardware_type: gv.HardwareType,
        level: gv.HardwareLevel | list[gv.HardwareLevel] | None = None,
    ) -> list[str]:
        """Get UIDs of hardware matching the given type and level.

        :param hardware_type: The type of hardware to filter by.
        :param level: The hardware level(s) to filter by, or None for all.
        :return: List of matching hardware UIDs.
        """
        level = level or [lvl for lvl in gv.HardwareLevel]
        if not isinstance(level, list):
            level = [level]
        return [
            uid
            for uid in self.hardware_dict
            if self.hardware_dict[uid]["type"] in hardware_type
               and self.hardware_dict[uid]["level"] in level
        ]

    def _create_new_short_uid(self) -> str:
        used_ids = {*self.hardware_dict.keys(), *self.plants_dict.keys()}
        while True:
            uid = create_uid(uid_length=16)
            if uid not in used_ids:
                return uid

    def _used_addresses(self) -> list[str]:
        return [
            self.hardware_dict[hardware]["address"]
            for hardware in self.hardware_dict
        ]

    @staticmethod
    def validate_hardware_dict(
            hardware_dict: gv.HardwareConfigDict,
            addresses_used: list,
    ) -> gv.HardwareConfigDict:
        """Validate a hardware configuration dictionary.

        Note: This method modifies hardware_dict in place, updating the
        'address' field with the resolved address.

        :param hardware_dict: The hardware configuration to validate.
        :param addresses_used: List of addresses already in use.
        :return: The validated (and possibly modified) hardware dict.
        :raises ValueError: If validation fails or address is already used.
        """
        try:
            hardware_config = gv.HardwareConfig(**hardware_dict)
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid hardware information provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`"
            )
        if hardware_config.model not in hardware_models:
            raise ValueError(
                "This hardware model is not supported. Use "
                "'EcosystemConfig.supported_hardware()' to see supported hardware."
            )
        hardware_cls = hardware_models[hardware_config.model]
        # Class initialization will later correct default address if needed
        hardware = hardware_cls._unsafe_from_config(hardware_config, None)
        # Replace default address with the actual address
        hardware_dict["address"] = hardware.address_repr
        if hardware_dict["address"] in addresses_used:
            raise ValueError(f"Address {hardware_config.address} already used.")
        return hardware_dict

    def create_new_hardware(
            self,
            *,
            name: str,
            address: str,
            model: str,
            type: str | gv.HardwareType,
            level: str | gv.HardwareLevel,
            groups: list[str] | set[str] | None = None,
            measures: list | None = None,
            plants: list | None = None,
            active: bool = True,
            multiplexer_model: str | None = None,
    ) -> None:
        """
        Create a new hardware
        :param name: str, the name of the hardware to create
        :param address: str: the address of the hardware to create
        :param model: str: the name of the model of the hardware to create
        :param type: str: the type of hardware to create ('sensor', 'light' ...)
        :param level: str: either 'environment' or 'plants'
        :param measures: list: the list of the measures taken
        :param plants: list: the name of the plant linked to the hardware
        :param active: bool: the status of the hardware. True (active/in use) by default.
        :param multiplexer_model: str: the model of the multiplexer used if there is one
        """
        uid = self._create_new_short_uid()
        hardware_dict = gv.HardwareConfigDict(**{
            "uid": uid,
            "name": name,
            "active": active,
            "address": address,
            "type": type,
            "level": level,
            "groups": groups,
            "model": model,
            "measures": measures,
            "plants": plants,
            "multiplexer_model": multiplexer_model,
        })
        hardware_dict = self.validate_hardware_dict(hardware_dict, self._used_addresses())
        uid = hardware_dict.pop("uid")
        self.hardware_dict.update({uid: hardware_dict})

    def update_hardware(
            self,
            uid: str,
            **updating_values: gv.AnonymousHardwareConfigDict,
    ) -> None:
        """Update an existing hardware configuration.

        :param uid: The UID of the hardware to update.
        :param updating_values: The values to update.
        :raises HardwareNotFound: If no hardware with the given UID exists.
        :raises ValueError: If the updated configuration is invalid.
        """
        if uid not in self.hardware_dict:
            raise HardwareNotFound(
                f"No hardware with uid '{uid}' found in the hardware config."
            )
        hardware_dict = self.hardware_dict[uid].copy()
        hardware_dict: gv.HardwareConfigDict = \
            cast(gv.HardwareConfigDict, hardware_dict)
        # Replace uid with a special uid for validation so it doesn't conflict
        # with existing hardware
        hardware_dict["uid"] = "__validation__"
        hardware_dict.update(
            {
                key: value
                for key, value in updating_values.items()
                if value is not None
            }
        )
        # Don't check address if not trying to update it. To do so, do not pass any address
        # against which to check
        used_addresses = self._used_addresses() if "address" in updating_values else []
        hardware_dict = self.validate_hardware_dict(hardware_dict, used_addresses)
        # Remove the validation uid from the dict
        hardware_dict.pop("uid")
        self.hardware_dict[uid] = hardware_dict

    def delete_hardware(self, uid: str) -> None:
        """
        Delete a hardware from the config
        :param uid: str, the uid of the hardware to delete
        """
        try:
            del self.hardware_dict[uid]
        except KeyError:
            raise HardwareNotFound(
                f"No hardware with uid '{uid}' found in the hardware config."
            )

    def get_hardware_uid(self, name: str) -> str:
        """Get the UID of a hardware by its name.

        :param name: The name of the hardware.
        :return: The UID of the hardware.
        :raises HardwareNotFound: If no hardware with the given name exists.
        """
        for uid, hardware in self.hardware_dict.items():
            if hardware["name"] == name:
                return uid
        raise HardwareNotFound(
            f"No hardware with name '{name}' found in the hardware config."
        )

    def get_hardware_config(self, uid: str) -> gv.HardwareConfig:
        """Get the full configuration for a hardware by its UID.

        :param uid: The UID of the hardware.
        :return: The hardware configuration.
        :raises HardwareNotFound: If no hardware with the given UID exists.
        """
        try:
            hardware_config = self.hardware_dict[uid]
            return gv.HardwareConfig(uid=uid, **hardware_config)
        except KeyError:
            raise HardwareNotFound(
                f"No hardware with uid '{uid}' found in the hardware config."
            )

    @staticmethod
    def supported_hardware() -> list[str]:
        """Return the list of supported hardware model names."""
        return [h for h in hardware_models]

    # ---------------------------------------------------------------------------
    #   Plants parameters
    # ---------------------------------------------------------------------------
    @property
    def plants_dict(self) -> dict[str, gv.AnonymousPlantConfigDict]:
        """
        Returns the plants present in the ecosystem
        """
        try:
            return self._config_dict["plants"]
        except KeyError:  # pragma: no cover
            self._config_dict["plants"] = {}
            return self._config_dict["plants"]

    def create_new_plant(
            self,
            *,
            name: str,
            species: str,
            sowing_date: datetime | None = None,
            hardware: list[str] | None = None,
    ) -> None:
        """
        Create a new plant
        :param name: str, the name of the plant to create
        :param species: str: the species of the plant to create
        :param sowing_date: datetime: the sowing date of the plant to create
        :param hardware: list: the name of the hardware linked to the plant
        """
        uid = self._create_new_short_uid()
        plant_dict = gv.PlantConfigDict(**{
            "uid": uid,
            "name": name,
            "species": species,
            "sowing_date": sowing_date,
            "hardware": hardware,
        })
        try:
            plant_dict = gv.PlantConfig(**plant_dict).model_dump()
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid plant information provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`"
            )
        uid = plant_dict.pop("uid")
        self.plants_dict.update({uid: plant_dict})

    def update_plant(
            self,
            uid: str,
            **updating_values: gv.AnonymousPlantConfigDict,
    ) -> None:
        """Update an existing plant configuration.

        :param uid: The UID of the plant to update.
        :param updating_values: The values to update.
        :raises PlantNotFound: If no plant with the given UID exists.
        :raises ValueError: If the updated configuration is invalid.
        """
        if uid not in self.plants_dict:
            raise PlantNotFound(
                f"No plant with uid '{uid}' found in the plant config."
            )
        plant_dict = self.plants_dict[uid].copy()
        plant_dict: gv.PlantConfigDict = cast(gv.PlantConfigDict, plant_dict)
        plant_dict["uid"] = uid
        plant_dict.update(
            {
                key: value
                for key, value in updating_values.items()
                if value is not gv.missing
            }
        )
        try:
            plant_dict = gv.PlantConfig(**plant_dict).model_dump()
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid plant information provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`"
            )
        uid = plant_dict.pop("uid")
        self.plants_dict[uid] = plant_dict

    def delete_plant(self, uid: str) -> None:
        """Delete a plant from the configuration.

        :param uid: The UID of the plant to delete.
        :raises PlantNotFound: If no plant with the given UID exists.
        """
        try:
            del self.plants_dict[uid]
        except KeyError:
            raise PlantNotFound(
                f"No plant with uid '{uid}' found in the plant config."
            )

    def get_plant_uid(self, name: str) -> str:
        """Get the UID of a plant by its name.

        :param name: The name of the plant.
        :return: The UID of the plant.
        :raises PlantNotFound: If no plant with the given name exists.
        """
        for uid, plant in self.plants_dict.items():
            if plant["name"] == name:
                return uid
        raise PlantNotFound(
            f"No plant with name '{name}' found in the plant config."
        )

    def get_plant_config(self, uid: str) -> gv.PlantConfig:
        """Get the full configuration for a plant by its UID.

        :param uid: The UID of the plant.
        :return: The plant configuration.
        :raises PlantNotFound: If no plant with the given UID exists.
        """
        try:
            plant_config = self.plants_dict[uid]
            return gv.PlantConfig(uid=uid, **plant_config)
        except KeyError:
            raise PlantNotFound(
                f"No plant with uid '{uid}' found in the plant config."
            )

# ---------------------------------------------------------------------------
#   Functions to interact with the module
# ---------------------------------------------------------------------------
def get_IDs(ecosystem: str) -> gv.IDs:
    """Return the tuple (ecosystem_uid, ecosystem_name)

    :param ecosystem: str, either an ecosystem uid or ecosystem name
    """
    return EngineConfig().get_IDs(ecosystem)
