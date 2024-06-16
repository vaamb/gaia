from __future__ import annotations

import asyncio
from asyncio import Condition, Event, Lock, sleep, Task
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
import hashlib
from json.decoder import JSONDecodeError
import logging
from math import pi, sin
from pathlib import Path
import random
import string
import typing as t
from typing import cast, Type, TypedDict, TypeVar
import weakref
from weakref import WeakValueDictionary

from anyio.to_thread import run_sync
import pydantic
from pydantic import Field, field_validator

import gaia_validators as gv
from gaia_validators import safe_enum_from_name

from gaia.config import (
    BaseConfig, configure_logging, GaiaConfig, GaiaConfigHelper)
from gaia.exceptions import (
    EcosystemNotFound, HardwareNotFound, UndefinedParameter)
from gaia.hardware import hardware_models
from gaia.subroutines import subroutine_dict
from gaia.utils import (
    get_sun_times, humanize_list, is_time_between, json, SingletonMeta, yaml)


if t.TYPE_CHECKING:
    from gaia.engine import Engine


def _to_dt(_time: time) -> datetime:
    # Transforms time to today's datetime. Needed to use timedelta
    _date = date.today()
    return datetime.combine(_date, _time)


def format_pydantic_error(error: pydantic.ValidationError) -> str:
    errors = error.errors()
    return ". ".join([
        f"{e['type'].replace('_', ' ').upper()} at parameter '{e['loc'][0]}', " \
        f"input '{e['input']}' is not valid."
        for e in errors
    ])


class ConfigType(Enum):
    ecosystems = "ecosystems.cfg"
    private = "private.cfg"


class CacheType(Enum):
    chaos = "chaos.json"


H = TypeVar("H", int, bytes, str)


def _file_checksum(file_path: Path, _buffer_size: int=4096) -> H:
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
    except FileNotFoundError:
        return b"\x00"


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
# Custom models for Climate and Environment configs as some of their
#  parameters are used as keys in ecosystems.cfg
class EnvironmentConfigValidator(gv.BaseModel):
    chaos: gv.ChaosConfig = Field(default_factory=gv.ChaosConfig)
    nycthemeral_cycle: gv.NycthemeralCycleConfig = Field(
        default_factory=gv.NycthemeralCycleConfig, validation_alias="sky")
    climate: dict[gv.ClimateParameter, gv.AnonymousClimateConfig] = \
        Field(default_factory=dict)

    @field_validator("climate", mode="before")
    def dict_to_climate(cls, value: dict):
        return {k: gv.AnonymousClimateConfig(**v) for k, v in value.items()}


class EnvironmentConfigDict(TypedDict):
    chaos: gv.ChaosConfigDict
    nycthemeral_cycle: gv.NycthemeralCycleConfigDict
    climate: dict[gv.ClimateParameter, gv.AnonymousClimateConfigDict]


class EcosystemBaseUpdateDict(TypedDict):
    name: str
    status: bool


class EcosystemConfigValidator(gv.BaseModel):
    name: str
    status: bool = False
    management: gv.ManagementConfig = Field(default_factory=gv.ManagementConfig)
    environment: EnvironmentConfigValidator = Field(default_factory=EnvironmentConfigValidator)
    IO: dict[str, gv.AnonymousHardwareConfig] = Field(default_factory=dict)


class EcosystemConfigDict(TypedDict):
    name: str
    status: bool
    management: dict[gv.ManagementNames, bool]
    environment: EnvironmentConfigDict
    IO: dict[str, gv.AnonymousHardwareConfigDict]


class RootEcosystemsConfigValidator(gv.BaseModel):
    config: dict[str, EcosystemConfigValidator]


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


class ChaosMemoryRootValidator(gv.BaseModel):
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
        self._sun_times: [str, SunTimesCacheData] = {}
        self._chaos_memory: dict[str, ChaosMemory] = {}
        # Watchdog threading securities
        self._config_files_checksum: dict[Path, H] = {}
        self._config_files_lock = Lock()
        self.new_config = Condition()
        self._stop_event = Event()
        self._task: Task | None = None
        self.configs_loaded: bool = False

    def __repr__(self) -> str:
        return f"EngineConfig(watchdog={self.started})"

    @property
    def started(self) -> bool:
        return self._task is not None

    @property
    def task(self) -> Task:
        if self._task is None:
            raise AttributeError("'thread' has not been set up")
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
    def engine(self, value: "Engine") -> None:
        self._engine = weakref.proxy(value)

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

    # Load, dump and save config
    def _check_files_lock_acquired(self) -> None:
        if not self._config_files_lock.locked():
            raise RuntimeError(
                "_load_config must be used within a "
                "`engine_config.with config_files_lock():` block"
            )

    def _load_config(self, cfg_type: ConfigType) -> None:
        # /!\ must be used with the config_files_lock acquired
        self._check_files_lock_acquired()
        config_path = self.get_file_path(cfg_type)
        if cfg_type == ConfigType.ecosystems:
            with open(config_path, "r") as file:
                unvalidated = yaml.load(file)
            try:
                validated: dict[str, EcosystemConfigDict] = \
                    RootEcosystemsConfigValidator(
                        **{"config": unvalidated}
                    ).model_dump()["config"]
            except pydantic.ValidationError as e:
                self.logger.error(
                    f"Could not validate ecosystems configuration file. "
                    f"ERROR msg(s): `{format_pydantic_error(e)}`."
                )
                raise e
            else:
                self._ecosystems_config_dict = self._validate_IO_dict(validated)
                self._dump_config(cfg_type)
        elif cfg_type == ConfigType.private:
            with open(config_path, "r") as file:
                unvalidated = yaml.load(file)
            try:
                validated = PrivateConfigValidator(
                    **unvalidated
                ).model_dump()
            except pydantic.ValidationError as e:
                self.logger.error(
                    f"Could not validate private configuration file. "
                    f"ERROR msg(s): `{format_pydantic_error(e)}`."
                )
                raise e
            else:
                self._private_config = validated

    @staticmethod
    def _validate_IO_dict(
            ecosystems_config_dict: dict[str, EcosystemConfigDict]
    ) -> dict[str, EcosystemConfigDict]:
        for ecosystem_name, ecosystem_dict in ecosystems_config_dict.items():
            validated_IO_dict: dict[str, gv.AnonymousHardwareConfigDict] = {}
            addresses_used = [
                hardware["name"] for hardware in ecosystem_dict["IO"].values()
            ]
            for IO_uid, IO_dict in ecosystem_dict["IO"].items():
                validated_hardware = EcosystemConfig.validate_hardware_dict(
                    hardware_dict={"uid": IO_uid, **IO_dict},
                    addresses_used=addresses_used,
                    check_address=True,
                    shorten=True,
                )
                validated_hardware.pop("uid")
                validated_IO_dict[IO_uid] = validated_hardware
            ecosystem_dict["IO"] = validated_IO_dict
        return ecosystems_config_dict

    def _dump_config(self, cfg_type: ConfigType):
        # /!\ must be used with the config_files_lock acquired
        self._check_files_lock_acquired()
        # TODO: shorten dicts used ?
        config_path = self.get_file_path(cfg_type)
        with open(config_path, "w") as file:
            if cfg_type == ConfigType.ecosystems:
                cfg = self.ecosystems_config_dict
            else:
                cfg = self._private_config
            yaml.dump(cfg, file)

    async def _create_ecosystems_config_file(self):
        self._ecosystems_config_dict = {}
        self._create_ecosystem("Default Ecosystem")
        await run_sync(self._dump_config, ConfigType.ecosystems)

    async def _create_private_config_file(self):
        self._private_config: PrivateConfigDict = \
            PrivateConfigValidator().model_dump()
        await run_sync(self._dump_config, ConfigType.private)

    async def initialize_configs(self) -> None:
        # This steps needs to remain separate and explicits as it loads files
        async with self.config_files_lock():
            for cfg_type in ConfigType:
                try:
                    await run_sync(self._load_config, cfg_type)
                except OSError:
                    if cfg_type == ConfigType.ecosystems:
                        self.logger.warning(
                            "No custom `ecosystems.cfg` configuration file "
                            "detected. Creating a default file.")
                        await self._create_ecosystems_config_file()
                    elif cfg_type == ConfigType.private:
                        self.logger.warning(
                            "No custom `private.cfg` configuration file "
                            "detected. Creating a default file.")
                        await self._create_private_config_file()
                finally:
                    file_path = self.get_file_path(cfg_type)
                    file_checksum = await run_sync(_file_checksum, file_path)
                    self._config_files_checksum[file_path] = file_checksum
        await self.load(CacheType.chaos)
        for ecosystem_uid, eco_cfg_dict in self.ecosystems_config_dict.items():
            eco_nyct_cfg = eco_cfg_dict["environment"]["nycthemeral_cycle"]
            ecosystem_name = self.get_IDs(ecosystem_uid).name
            self.logger.debug(
                f"Checking if lighting and nycthemeral span methods for "
                f"ecosystem {ecosystem_name} are possible.")
            lighting_method = safe_enum_from_name(
                gv.LightingMethod, eco_nyct_cfg["lighting"])
            self.check_nycthemeral_method_validity(ecosystem_uid, lighting_method)
            span_method = safe_enum_from_name(
                gv.NycthemeralSpanMethod, eco_nyct_cfg["span"])
            self.check_nycthemeral_method_validity(ecosystem_uid, span_method)
        self.configs_loaded = True

    async def save(self, cfg_type: ConfigType | CacheType) -> None:
        if self.app_config.TESTING:
            return
        if isinstance(cfg_type, ConfigType):
            async with self.config_files_lock():
                self.logger.debug(f"Updating {cfg_type.name} configuration file(s).")
                await run_sync(self._dump_config, cfg_type)
        else:
            if cfg_type == CacheType.chaos:
                await run_sync(self._dump_chaos_memory)

    async def load(self, cfg_type: ConfigType | CacheType) -> None:
        if isinstance(cfg_type, ConfigType):
            async with self.config_files_lock():
                self.logger.debug(f"Loading {cfg_type.name} configuration file(s).")
                await run_sync(self._load_config, cfg_type)
        else:
            if cfg_type == CacheType.chaos:
                await run_sync(self._load_chaos_memory)

    # File watchdog
    async def _get_changed_config_files(self) -> set[ConfigType]:
        config_files_checksum: dict[Path, H] = {}
        changed: set[ConfigType] = set()
        for file_path, file_modif in self._config_files_checksum.items():
            modif = await run_sync(_file_checksum, file_path)
            if modif != file_modif:
                changed.add(ConfigType(file_path.name))
            config_files_checksum[file_path] = modif
        self._config_files_checksum = config_files_checksum
        return changed

    async def _watchdog_routine(self) -> None:
        # Fill config files modification dict
        async with self.config_files_lock_no_reset():
            changed_configs = await self._get_changed_config_files()
            if changed_configs:
                for config_type in changed_configs:
                    self.logger.info(
                        f"Change in '{config_type.value}' detected. Updating "
                        f"{config_type.name} configuration.")
                    self._load_config(cfg_type=config_type)
                if ConfigType.ecosystems in changed_configs:
                    for ecosystem_config in self.ecosystems_config.values():
                        ecosystem_config.reset_caches()
                with self.new_config:
                    self.new_config.notify_all()
                    # This unblocks the engine loop. It will then refresh
                    #  ecosystems, update sun times, ecosystem lighting hours
                    #  and send the data if it is connected.

    async def _watchdog_loop(self) -> None:
        sleep_period = self.app_config.CONFIG_WATCHER_PERIOD / 1000
        self.logger.info(
            f"Starting the configuration file watchdog loop. It will run every "
            f"{sleep_period:.3f} s.")
        while not self._stop_event.is_set():
            try:
                await self._watchdog_routine()
            except Exception as e:
                self.logger.error(
                    f"Encountered an error while running the watchdog routine. "
                    f"ERROR msg: `{e.__class__.__name__} :{e}`."
                )
            await sleep(sleep_period)

    def start_watchdog(self) -> None:
        if not self.configs_loaded:  # pragma: no cover
            raise RuntimeError(
                "Configuration files need to be loaded in order to start "
                "the config file watchdog. To do so, use the "
                "`EngineConfig().initialize_configs()` method."
            )

        if self.started:  # pragma: no cover
            raise RuntimeError("Configuration files watchdog is already running")

        self.logger.info("Starting the configuration files watchdog")
        self.task = asyncio.create_task(
            self._watchdog_loop(),
            name="Config_WatchdogLoopThread",
        )
        self.logger.debug("Configuration files watchdog successfully started")

    def stop_watchdog(self) -> None:
        if not self.started:  # pragma: no cover
            raise RuntimeError("Configuration files watchdog is not running")

        self.logger.info("Stopping the configuration files watchdog")
        self._stop_event.set()
        self.task = None
        self.logger.debug("Configuration files watchdog successfully stopped")

    @asynccontextmanager
    async def config_files_lock_no_reset(self):
        async with self._config_files_lock:
            yield

    @asynccontextmanager
    async def config_files_lock(self):
        """A context manager that makes sure only one process access file
        content at the time"""
        async with self.config_files_lock_no_reset():
            try:
                yield
            finally:
                await self._get_changed_config_files()

    # API
    def _create_new_ecosystem_uid(self) -> str:
        length = 8
        used_ids = self.ecosystems_uid
        while True:
            x = random.choice(string.ascii_letters) + "".join(
                random.choices(string.ascii_letters + string.digits,
                               k=length-1))
            if x not in used_ids:
                break
        return x

    def _create_ecosystem(self, ecosystem_name: str) -> None:
        uid = self._create_new_ecosystem_uid()
        ecosystem_cfg = EcosystemConfigValidator(name=ecosystem_name).model_dump()
        self.ecosystems_config_dict.update({uid: ecosystem_cfg})

    def create_ecosystem(self, ecosystem_name: str) -> None:
        self._create_ecosystem(ecosystem_name)

    def update_ecosystem(
            self,
            ecosystem_id: str,
            **updating_values: EcosystemBaseUpdateDict,
    ) -> None:
        ecosystem_ids = self.get_IDs(ecosystem_id)
        ecosystem = self.ecosystems_config_dict.get(ecosystem_ids.uid)
        # Make extra sure no "complex" field is overridden
        updating_values.pop("management", None)
        updating_values.pop("environment", None)
        updating_values.pop("IO", None)
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

    def get_ecosystems_expected_to_run(self) -> set:
        return set([
            ecosystem_uid
            for ecosystem_uid, eco_cfg_dict in self.ecosystems_config_dict.items()
            if eco_cfg_dict["status"]
        ])

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
                f"configuration file.")
        self.set_place(place, coordinates)

    def delete_place(self, place: str) -> None:
        try:
            del self.places[place]
        except KeyError:
            raise UndefinedParameter(
                f"No location named '{place}' was found in the private "
                f"configuration file.")

    @property
    def home_coordinates(self) -> gv.Coordinates:
        home = self.get_place("home")
        if home is None:
            raise UndefinedParameter(
                f"No location named 'home' was found in the private "
                f"configuration file.")
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
            new_sun_times = get_sun_times(coord.longitude, coord.latitude)
            # Handle high and low latitude specificities
            if (
                    # Range of polar day in Northern hemisphere
                    3 < today.month <= 9 and coord.latitude > 0
                    # Range of polar day in Southern hemisphere
                    or not (3 < today.month <= 9) and coord.latitude < 0
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
        validated_sun_times: gv.SunTimesDict = gv.SunTimes(
            **sun_times).model_dump()
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

    def check_nycthemeral_method_validity(
            self,
            ecosystem_uid: str,
            method: gv.LightingMethod | gv.NycthemeralSpanMethod,
    ) -> bool:
        ecosystem_name = self.get_IDs(ecosystem_uid).name
        if method == 0:  # Fixed, no target needed
            return True
        # Try to get the target
        target: str
        if (method & gv.LightingMethod.elongate) == gv.LightingMethod.elongate:
            target = "home"
        elif (method & gv.NycthemeralSpanMethod.mimic) == gv.NycthemeralSpanMethod.mimic:
            nyct_cfg: gv.NycthemeralCycleConfigDict = \
                self.ecosystems_config_dict[ecosystem_uid]["environment"]["nycthemeral_cycle"]
            target = nyct_cfg.get("target")
            if target is None:
                self.logger.warning(
                    f"Nycthemeral span method method for ecosystem "
                    f"{ecosystem_name} cannot be 'mimic' as no target is "
                    f"specified in the ecosystems configuration file."
                )
                return False
        else:
            raise ValueError(
                "'method' should be either a valid lighting method or a valid "
                "nycthemeral span method.")

        m = 'Lighting' if isinstance(method, gv.LightingMethod) else 'Nycthemeral span'
        # Try to get the target's coordinates
        place = self.get_place(target)
        if place is None:
            self.logger.warning(
                f"{m} method for ecosystem {ecosystem_name} cannot be "
                f"'{method.name}' as the coordinates of '{target}' is "
                f"not provided in the private configuration file. Will fall "
                f"back to 'fixed'."
            )
            return False
        # Try to get the target's sun times
        sun_times = self.get_sun_times(target)
        if sun_times is None:
            self.logger.warning(
                f"{m} method for ecosystem {ecosystem_name} cannot be "
                f"'{method.name}' as the sun times of '{target}' "
                f"weren't found. Will fall back to 'fixed'."
            )
            return False
        return True

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

    def _load_chaos_memory(self) -> None:
        self.logger.debug("Trying to load chaos memory.")
        chaos_path = self.get_file_path(CacheType.chaos)
        validated: dict[str, ChaosMemory]
        try:
            with chaos_path.open("r") as file:
                unvalidated = json.loads(file.read())
                try:
                    validated = ChaosMemoryRootValidator(
                        root=unvalidated
                    ).model_dump()["root"]
                except pydantic.ValidationError:
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
            self._dump_chaos_memory()

    def _dump_chaos_memory(self) -> None:
        chaos_path = self.get_file_path(CacheType.chaos)
        with chaos_path.open("w") as file:
            file.write(json.dumps(self._chaos_memory))

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
            except IndexError:
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
            engine_config: EngineConfig | None = None
    ) -> None:
        engine_config = engine_config or EngineConfig()
        self._engine_config: EngineConfig = engine_config
        ids = self._engine_config.get_IDs(ecosystem_id)
        self.uid = ids.uid
        name = ids.name.replace(" ", "_")
        self.logger = logging.getLogger(f"gaia.engine.{name}.config")
        #self._nycthemeral_hours_lock = Lock()  #TODO: was an RLock, check if ok
        self._nycthemeral_span_method_cache: gv.NycthemeralSpanMethod | None = None
        self._nycthemeral_span_hours_data: gv.NycthemeralSpanConfig | None = None
        self._lighting_method_cache: gv.LightingMethod | None = None
        self._lighting_hours_data: gv.LightingHours | None = None

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.uid}, name={self.name}, " \
               f"engine_config={self._engine_config})"

    @property
    def __dict(self) -> EcosystemConfigDict:
        return self._engine_config.ecosystems_config_dict[self.uid]

    def as_dict(self) -> EcosystemConfigDict:
        return self.__dict

    async def save(self) -> None:
        await self._engine_config.save(ConfigType.ecosystems)

    def reset_nycthemeral_caches(self) -> None:
        self._nycthemeral_span_method_cache = None
        self._nycthemeral_span_hours_cache = None
        self._lighting_method_cache = None
        self._lighting_hours_cache = None

    def reset_caches(self) -> None:
        self.reset_nycthemeral_caches()

    @property
    def general(self) -> EngineConfig:
        return self._engine_config

    @property
    def name(self) -> str:
        return self.__dict["name"]

    @name.setter
    def name(self, value: str) -> None:
        self.__dict["name"] = value

    @property
    def status(self) -> bool:
        return self.__dict["status"]

    @status.setter
    def status(self, value: bool) -> None:
        self.__dict["status"] = value

    """Parameters related to sub-routines control"""
    @property
    def managements(self) -> gv.ManagementConfigDict:
        return self.__dict["management"]

    @managements.setter
    def managements(self, value: gv.ManagementConfigDict) -> None:
        self.__dict["management"] = gv.ManagementConfig(**value).model_dump()

    @property
    def management_flag(self) -> int:
        management_config = gv.ManagementConfig(**self.managements)
        return management_config.to_flag()

    def get_management(
            self,
            management: gv.ManagementNames | gv.ManagementFlags,
    ) -> bool:
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
            management: gv.ManagementNames | gv.ManagementFlags,
            value: bool,
    ) -> None:
        validated_management = safe_enum_from_name(gv.ManagementFlags, management)
        management_name: gv.ManagementNames = validated_management.name
        self.__dict["management"][management_name] = value

    def get_subroutines_enabled(self) -> list[gv.ManagementNames]:
        return [subroutine for subroutine in subroutine_dict
                if self.get_management(subroutine)]

    """EnvironmentConfig related parameters"""
    @property
    def environment(self) -> EnvironmentConfigDict:
        """
        Returns the environment config for the ecosystem
        """
        try:
            return self.__dict["environment"]
        except KeyError:
            self.__dict["environment"] = EnvironmentConfigValidator().model_dump()
            return self.__dict["environment"]

    @property
    def nycthemeral_cycle(self) -> gv.NycthemeralCycleConfigDict:
        """
        Returns the nycthemeral cycle config for the ecosystem
        """
        try:
            return self.environment["nycthemeral_cycle"]
        except KeyError:
            self.environment["nycthemeral_cycle"] = gv.NycthemeralCycleConfig().model_dump()
            return self.environment["nycthemeral_cycle"]

    @property
    def nycthemeral_span_target(self) -> str | None:
        return self.nycthemeral_cycle["target"]

    async def set_nycthemeral_span_target(self, target: str | None) -> None:
        place = self.general.get_place(target)
        if place is None:
            raise ValueError(
                "The place targeted must first be set with "
                "`EngineConfig.set_place` before using it as a target."
            )
        self.nycthemeral_cycle["target"] = target
        self.reset_nycthemeral_caches()
        await self.refresh_lighting_hours(send_info=True)

    @property
    def nycthemeral_span_method(self) -> gv.NycthemeralSpanMethod:
        if self._nycthemeral_span_method_cache is None:
            span_method: gv.NycthemeralSpanMethod = safe_enum_from_name(
                gv.NycthemeralSpanMethod, self.nycthemeral_cycle["span"])
            if self.general.app_config.TESTING:
                self._nycthemeral_span_method_cache = span_method
            # If using fixed method, no check required
            elif span_method & gv.NycthemeralSpanMethod.fixed:
                self._nycthemeral_span_method_cache = gv.NycthemeralSpanMethod.fixed
            # Else, we need to make sure we have suntimes for the nycthemeral target
            else:
                target = self.nycthemeral_span_target
                sun_times = self.general.get_sun_times(target)
                if sun_times is None:
                    self._nycthemeral_span_method_cache = gv.NycthemeralSpanMethod.fixed
                else:
                    self._nycthemeral_span_method_cache = gv.NycthemeralSpanMethod.mimic
        return self._nycthemeral_span_method_cache

    async def set_nycthemeral_span_method(self, method: gv.NycthemeralSpanMethod) -> None:
        method = safe_enum_from_name(gv.NycthemeralSpanMethod, method)
        method_is_valid = self.general.check_nycthemeral_method_validity(self.uid, method)
        if not method_is_valid:
            raise ValueError(
                    f"Cannot set nycthemeral span method to '{method.name}'. "
                    f"See the logs to see the reason."
                )
        self.nycthemeral_cycle["span"] = method
        # self.reset_nycthemeral_caches()  # Done in refresh_lighting_hours()
        await self.refresh_lighting_hours(send_info=True)

    @property
    def _nycthemeral_span_hours_cache(self) -> gv.NycthemeralSpanConfig | None:
        #with self._nycthemeral_hours_lock:
        return self._nycthemeral_span_hours_data

    @_nycthemeral_span_hours_cache.setter
    def _nycthemeral_span_hours_cache(
            self,
            span_hours: gv.NycthemeralSpanConfig | None,
    ) -> None:
        #with self._nycthemeral_hours:
        self._nycthemeral_span_hours_data = span_hours

    @property
    def nycthemeral_span_hours(self) -> gv.NycthemeralSpanConfig:
        if self._nycthemeral_span_hours_cache is None:
            if self.nycthemeral_span_method == gv.NycthemeralSpanMethod.mimic:
                target = self.nycthemeral_span_target
                sun_times = self.general.get_sun_times(target)
                if sun_times is not None:
                    self._nycthemeral_span_hours_cache = gv.NycthemeralSpanConfig(
                        day=sun_times["sunrise"],
                        night=sun_times["sunset"],
                    )
            self._nycthemeral_span_hours_cache = gv.NycthemeralSpanConfig(
                day=self.nycthemeral_cycle["day"],
                night=self.nycthemeral_cycle["night"],
            )
        return self._nycthemeral_span_hours_cache

    async def set_nycthemeral_span_hours(self, value: gv.NycthemeralSpanConfigDict) -> None:
        """Set time parameters

        :param value: A dict in the form {'day': '8h00', 'night': '22h00'}
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
        await self.refresh_lighting_hours(send_info=True)

    @property
    def period_of_day(self) -> gv.PeriodOfDay:
        nycthemeral_span = self.nycthemeral_span_hours
        if is_time_between(
                nycthemeral_span.day,
                nycthemeral_span.night,
                datetime.now().time()
        ):
            return gv.PeriodOfDay.day
        return gv.PeriodOfDay.night

    @property
    def lighting_method(self) -> gv.LightingMethod:
        if self._lighting_method_cache is None:
            lighting_method: gv.LightingMethod = safe_enum_from_name(
                gv.LightingMethod, self.nycthemeral_cycle["lighting"])
            if self.general.app_config.TESTING:
                self._lighting_method_cache = lighting_method
            # If using fixed method, no check required
            elif lighting_method & gv.LightingMethod.fixed:
                self._lighting_method_cache = gv.LightingMethod.fixed
            # Else, we need to make sure we have suntimes for "home"
            else:
                sun_times = self.general.get_sun_times("home")
                if sun_times is None:
                    self._lighting_method_cache = gv.LightingMethod.fixed
                else:
                    self._lighting_method_cache = gv.LightingMethod.elongate
        return self._lighting_method_cache

    @lighting_method.setter
    def lighting_method(self, light_method: gv.LightingMethod) -> None:
        if not self.general.app_config.TESTING:
            raise AttributeError("can't set attribute 'light_method'")
        self.nycthemeral_cycle["lighting"] = light_method
        self._lighting_method_cache = None

    async def set_lighting_method(self, method: gv.LightingMethod) -> None:
        method = safe_enum_from_name(gv.LightingMethod, method)
        method_is_valid = self.general.check_nycthemeral_method_validity(self.uid, method)
        if not method_is_valid:
            raise ValueError(
                    f"Cannot set light method to '{method.name}'. Look at the "
                    f"logs to see the reason."
                )
        self.nycthemeral_cycle["lighting"] = method
        # self.reset_nycthemeral_caches()  # Done in refresh_lighting_hours()
        await self.refresh_lighting_hours(send_info=True)

    @property
    def _lighting_hours_cache(self) -> gv.LightingHours | None:
        #with self._nycthemeral_hours_lock:
        return self._lighting_hours_data

    @_lighting_hours_cache.setter
    def _lighting_hours_cache(self, lighting_hours: gv.LightingHours | None) -> None:
        #with self._nycthemeral_hours_lock:
        self._lighting_hours_data = lighting_hours

    @property
    def lighting_hours(self) -> gv.LightingHours:
        if self._lighting_hours_cache is None:
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

            self._lighting_hours_cache = gv.LightingHours(
                morning_start=morning_start,
                # Morning should not end later than evening
                morning_end=min(morning_end, evening_end),
                # Evening should not start before morning
                evening_start=max(evening_start, morning_start),
                evening_end=evening_end,
            )
        return self._lighting_hours_cache

    @lighting_hours.setter
    def lighting_hours(self, lighting_hours: gv.LightingHours) -> None:
        if not self.general.app_config.TESTING:
            raise AttributeError(
                "'lighting_hours' can only be set when 'TESTING' is True.")
        self._lighting_hours_cache = lighting_hours
        # DO NOT USE THIS as it will overwrite the newly set value
        # self.reset_nycthemeral_caches()

    async def refresh_lighting_hours(self, send_info: bool = True) -> None:
        self.logger.info("Refreshing lighting hours.")
        self.reset_nycthemeral_caches()
        # Check we've got the info required
        # Then update info using lock as the whole dict should be transformed at the "same time"

        # Log warnings for issues with raw nycthemeral span method
        self.general.check_nycthemeral_method_validity(
            self.uid, self.nycthemeral_cycle["span"])
        # Raw nycthemeral span is silently replaced if needed
        self.nycthemeral_span_method
        self.nycthemeral_span_hours

        # Log warnings for issues with raw lighting method
        self.general.check_nycthemeral_method_validity(
            self.uid, self.nycthemeral_cycle["lighting"])
        # Raw lighting method span is silently replaced if needed
        self.lighting_method
        self.lighting_hours

        if (
                send_info
                and self.general.engine_set_up
                and self.general.engine.use_message_broker
        ):
            try:
                await self.general.engine.event_handler.send_payload_if_connected(
                    "light_data", ecosystem_uids=[self.uid])
            except Exception as e:
                self.logger.error(
                    f"Encountered an error while sending light data. "
                    f"ERROR msg: `{e.__class__.__name__} :{e}`"
                )

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
        except pydantic.ValidationError as e:
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
        self.logger.info("Updating chaos time window.")
        if self.general.get_chaos_memory(self.uid)["last_update"] < date.today():
            await self._update_chaos_time_window()
            if (
                    send_info
                    and self.general.engine_set_up
                    and self.general.engine.use_message_broker
            ):
                await self.general.engine.event_handler.send_payload_if_connected(
                    "chaos_parameters")
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

    @property
    def climate(self) -> dict[gv.ClimateParameter, gv.AnonymousClimateConfigDict]:
        """
        Returns the climate config for the ecosystem
        """
        try:
            return self.environment["climate"]
        except KeyError:
            self.environment["climate"] = {}
            return self.environment["climate"]

    def get_climate_parameter(
            self,
            parameter: gv.ClimateParameter | gv.ClimateParameterNames,
    ) -> gv.ClimateConfig:
        parameter = safe_enum_from_name(gv.ClimateParameter, parameter)
        try:
            data = self.climate[parameter]
            return gv.ClimateConfig(parameter=parameter, **data)
        except KeyError:
            raise UndefinedParameter(
                f"No climate parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file.")

    def set_climate_parameter(
            self,
            parameter: gv.ClimateParameter | gv.ClimateParameterNames,
            **value: gv.AnonymousClimateConfigDict,
    ) -> None:
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
            parameter: gv.ClimateParameter | gv.ClimateParameterNames,
            **value: gv.AnonymousClimateConfigDict,
    ) -> None:
        parameter = safe_enum_from_name(gv.ClimateParameter, parameter)
        if not self.climate.get(parameter):
            raise UndefinedParameter(
                f"No climate parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file.")
        self.set_climate_parameter(parameter, **value)

    def delete_climate_parameter(
            self,
            parameter: gv.ClimateParameter | gv.ClimateParameterNames,
    ) -> None:
        parameter = safe_enum_from_name(gv.ClimateParameter, parameter)
        try:
            del self.climate[parameter]
        except KeyError:
            raise UndefinedParameter(
                f"No climate parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file")

    """Parameters related to IO"""    
    @property
    def IO_dict(self) -> dict[str, gv.AnonymousHardwareConfigDict]:
        """
        Returns the IOs (hardware) present in the ecosystem
        """
        try:
            return self.__dict["IO"]
        except KeyError:
            self.__dict["IO"] = {}
            return self.__dict["IO"]

    def get_IO_group_uids(
            self,
            IO_type: gv.HardwareType,
            level: tuple[gv.HardwareLevel] = (
                    gv.HardwareLevel.environment, gv.HardwareLevel.plants)
    ) -> list[str]:
        return [uid for uid in self.IO_dict
                if self.IO_dict[uid]["type"] == IO_type
                and self.IO_dict[uid]["level"] in level]

    def _create_new_IO_uid(self) -> str:
        length = 16
        used_ids = set(self.IO_dict.keys())
        while True:
            x = random.choice(string.ascii_letters) + "".join(
                random.choices(string.ascii_letters + string.digits,
                               k=length - 1))
            if x not in used_ids:
                break
        return x

    def _used_addresses(self):
        return [self.IO_dict[hardware]["address"]
                for hardware in self.IO_dict]

    @staticmethod
    def validate_hardware_dict(
            hardware_dict: gv.HardwareConfigDict,
            addresses_used: list,
            check_address: bool = True,
            shorten: bool = False,
    ) -> gv.HardwareConfigDict:
        try:
            hardware_config = gv.HardwareConfig(**hardware_dict)
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid hardware information provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`"
            )
        if hardware_config.address.lower() == "i2c_default":
            check_address = False
        if (
                check_address
                and hardware_config.address in addresses_used
        ):
            raise ValueError(f"Address {hardware_config.address} already used.")
        if hardware_config.model not in hardware_models:
            raise ValueError(
                "This hardware model is not supported. Use "
                "'EcosystemConfig.supported_hardware()' to see supported hardware."
            )
        hardware_cls = hardware_models[hardware_config.model]
        hardware = hardware_cls.from_hardware_config(hardware_config, None)
        return hardware.dict_repr(shorten)

    def create_new_hardware(
            self,
            *,
            name: str,
            address: str,
            model: str,
            type: gv.HardwareTypeNames,
            level: gv.HardwareLevelNames,
            measures: list | None = None,
            plants: list | None = None,
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
        :param multiplexer_model: str: the model of the multiplexer used if there is one
        """
        uid = self._create_new_IO_uid()
        hardware_dict = gv.HardwareConfigDict(**{
            "uid": uid,
            "name": name,
            "address": address,
            "type": type,
            "level": level,
            "model": model,
            "measures": measures,
            "plants": plants,
            "multiplexer_model": multiplexer_model,
        })
        hardware_dict = self.validate_hardware_dict(
            hardware_dict, self._used_addresses(), shorten=True)
        hardware_dict.pop("uid")
        self.IO_dict.update({uid: hardware_dict})

    def update_hardware(
            self,
            uid: str,
            **updating_values: gv.AnonymousHardwareConfigDict
    ) -> None:
        base_hardware_dict = self.IO_dict.get(uid)
        if base_hardware_dict is None:
            raise HardwareNotFound(
                f"No hardware with uid '{uid}' found in the hardware config.")
        hardware_dict = base_hardware_dict.copy()
        hardware_dict: gv.HardwareConfigDict = cast(gv.HardwareConfigDict, hardware_dict)
        hardware_dict["uid"] = uid
        hardware_dict.update({
            key: value for key, value in updating_values.items()
            if value is not None
        })
        check_address = "address" in updating_values  # Don't check address if not trying to update it
        hardware_dict = self.validate_hardware_dict(
            hardware_dict, self._used_addresses(), check_address, shorten=True)
        hardware_dict.pop("uid")
        self.IO_dict[uid] = hardware_dict

    def delete_hardware(self, uid: str) -> None:
        """
        Delete a hardware from the config
        :param uid: str, the uid of the hardware to delete
        """
        try:
            del self.IO_dict[uid]
        except KeyError:
            raise HardwareNotFound(
                f"No hardware with uid '{uid}' found in the hardware config.")

    def get_hardware_uid(self, name: str) -> str:
        for uid, hardware in self.IO_dict.items():
            if hardware["name"] == name:
                return uid
        raise HardwareNotFound(
                f"No hardware with name '{name}' found in the hardware config.")

    def get_hardware_config(self, uid: str) -> gv.HardwareConfig:
        try:
            hardware_config = self.IO_dict[uid]
            return gv.HardwareConfig(uid=uid, **hardware_config)
        except KeyError:
            raise HardwareNotFound(
                f"No hardware with uid '{uid}' found in the hardware config.")

    @staticmethod
    def supported_hardware() -> list:
        return [h for h in hardware_models]


# ---------------------------------------------------------------------------
#   Functions to interact with the module
# ---------------------------------------------------------------------------
def get_IDs(ecosystem: str) -> gv.IDs:
    """Return the tuple (ecosystem_uid, ecosystem_name)

    :param ecosystem: str, either an ecosystem uid or ecosystem name
    """
    return EngineConfig().get_IDs(ecosystem)
