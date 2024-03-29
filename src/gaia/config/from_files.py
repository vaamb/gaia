from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
import hashlib
from json.decoder import JSONDecodeError
import logging
import os
from math import pi, sin
from pathlib import Path
import random
import string
from threading import Condition, Event, Lock, Thread
import typing as t
from typing import cast, Literal, Type, TypedDict, TypeVar
import weakref
from weakref import WeakValueDictionary

import pydantic
from pydantic import Field, field_validator, ValidationError
from requests import ConnectionError, Session

import gaia_validators as gv
from gaia_validators import safe_enum_from_name

from gaia.config import (
    BaseConfig, configure_logging, GaiaConfig, GaiaConfigHelper)
from gaia.exceptions import (
    EcosystemNotFound, HardwareNotFound, UndefinedParameter)
from gaia.hardware import Hardware, hardware_models
from gaia.subroutines import subroutine_dict
from gaia.utils import humanize_list, json, SingletonMeta, yaml


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
    sun_times = "sun_time.json"


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
DaytimeEvents = Literal[
    "civil_twilight_begin", "sunrise", "sunset", "civil_twilight_end"]


class SunTimesCacheValidator(gv.LaxBaseModel):
    last_update: date
    data: gv.SunTimes


class SunTimesCacheData(TypedDict):
    last_update: date
    data: gv.SunTimesDict


class RootSunTimesCacheValidator(gv.BaseModel):
    config: dict[str, SunTimesCacheValidator]


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
    sky: gv.SkyConfig = Field(default_factory=gv.SkyConfig)
    climate: dict[gv.ClimateParameter, gv.AnonymousClimateConfig] = \
        Field(default_factory=dict)

    @field_validator("climate", mode="before")
    def dict_to_climate(cls, value: dict):
        return {k: gv.AnonymousClimateConfig(**v) for k, v in value.items()}


class EnvironmentConfigDict(TypedDict):
    chaos: gv.ChaosConfigDict
    sky: gv.SkyConfigDict
    climate: dict[gv.ClimateParameter, gv.AnonymousClimateConfigDict]


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
        self._engine: "Engine" | None = None
        self._ecosystems_config_dict: dict[str, EcosystemConfigDict] = {}
        self._private_config: PrivateConfigDict = PrivateConfigValidator().model_dump()
        self._sun_times: [str, SunTimesCacheData] = {}
        self._chaos_memory: dict[str, ChaosMemory] = {}
        # Watchdog threading securities
        self._config_files_checksum: dict[Path, H] = {}
        self._config_files_lock = Lock()
        self.new_config = Condition()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self.configs_loaded: bool = False

    def __repr__(self) -> str:
        return f"EngineConfig(watchdog={self.started})"

    @property
    def started(self) -> bool:
        return self._thread is not None

    @property
    def thread(self) -> Thread:
        if self._thread is None:
            raise AttributeError("'thread' has not been set up")
        return self._thread

    @thread.setter
    def thread(self, thread: Thread | None) -> None:
        self._thread = thread

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

    def _create_ecosystems_config_file(self):
        self._ecosystems_config_dict = {}
        self._create_ecosystem("Default Ecosystem")
        self._dump_config(ConfigType.ecosystems)

    def _create_private_config_file(self):
        self._private_config: PrivateConfigDict = \
            PrivateConfigValidator().model_dump()
        self._dump_config(ConfigType.private)

    def initialize_configs(self) -> None:
        # This steps needs to remain separate and explicits as it loads files
        with self.config_files_lock():
            for cfg_type in ConfigType:
                try:
                    self._load_config(cfg_type)
                except OSError:
                    if cfg_type == ConfigType.ecosystems:
                        self.logger.warning(
                            "No custom `ecosystems.cfg` configuration file "
                            "detected. Creating a default file.")
                        self._create_ecosystems_config_file()
                    elif cfg_type == ConfigType.private:
                        self.logger.warning(
                            "No custom `private.cfg` configuration file "
                            "detected. Creating a default file.")
                        self._create_private_config_file()
                finally:
                    file_path = self.get_file_path(cfg_type)
                    self._config_files_checksum[file_path] = _file_checksum(file_path)
        self.load(CacheType.chaos)
        self.load(CacheType.sun_times)
        for ecosystem_uid, eco_cfg_dict in self.ecosystems_config_dict.items():
            light_method = safe_enum_from_name(
                gv.LightMethod, eco_cfg_dict["environment"]["sky"]["lighting"])
            ecosystem_name = self.get_IDs(ecosystem_uid).name
            self.logger.debug(
                f"Checking if light method for ecosystem {ecosystem_name} is possible.")
            light_is_method_valid = self.check_lighting_method_validity(
                ecosystem_uid, light_method)
            if not light_is_method_valid:
                self.logger.warning(
                    f"Light method '{light_method.name}' is not a valid option "
                    f"for ecosystem '{ecosystem_name}'. Will fall back to "
                    f"'fixed'."
                )
        self.save(CacheType.sun_times)
        self.configs_loaded = True

    def save(self, cfg_type: ConfigType | CacheType) -> None:
        if self.app_config.TESTING:
            return
        if isinstance(cfg_type, ConfigType):
            with self.config_files_lock():
                self.logger.debug(f"Updating {cfg_type.name} configuration file(s).")
                self._dump_config(cfg_type)
        else:
            if cfg_type == CacheType.chaos:
                self._dump_chaos_memory()
            elif cfg_type == CacheType.sun_times:
                self._dump_sun_times()

    def load(self, cfg_type: ConfigType | CacheType) -> None:
        if isinstance(cfg_type, ConfigType):
            with self.config_files_lock():
                self.logger.debug(f"Loading {cfg_type.name} configuration file(s).")
                self._load_config(cfg_type)
        else:
            if cfg_type == CacheType.chaos:
                self._load_chaos_memory()
            elif cfg_type == CacheType.sun_times:
                self._load_cached_sun_times()

    # File watchdog
    def _get_changed_config_files(self) -> set[ConfigType]:
        config_files_checksum: dict[Path, H] = {}
        changed: set[ConfigType] = set()
        for file_path, file_modif in self._config_files_checksum.items():
            modif = _file_checksum(file_path)
            if modif != file_modif:
                changed.add(ConfigType(file_path.name))
            config_files_checksum[file_path] = modif
        self._config_files_checksum = config_files_checksum
        return changed

    def _watchdog_routine(self) -> None:
        # Fill config files modification dict
        with self.config_files_lock_no_reset():
            changed_configs = self._get_changed_config_files()
            if changed_configs:
                for config_type in changed_configs:
                    self.logger.info(
                        f"Change in '{config_type.value}' detected. Updating "
                        f"{config_type.name} configuration.")
                    self._load_config(cfg_type=config_type)
                with self.new_config:
                    self.new_config.notify_all()
                    # This unblocks the engine loop. It will then refresh
                    #  ecosystems, update sun times, ecosystem lighting hours
                    #  and send the data if it is connected.

    def _watchdog_loop(self) -> None:
        sleep_period = self.app_config.CONFIG_WATCHER_PERIOD / 1000
        self.logger.info(
            f"Starting the configuration file watchdog loop. It will run every "
            f"{sleep_period:.3f} s.")
        while not self._stop_event.is_set():
            try:
                self._watchdog_routine()
            except Exception as e:
                self.logger.error(
                    f"Encountered an error while running the watchdog routine. "
                    f"ERROR msg: `{e.__class__.__name__} :{e}`."
                )
            self._stop_event.wait(sleep_period)

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
        self.thread = Thread(
            target=self._watchdog_loop,
            name="Config_WatchdogLoopThread",
            daemon=True,
        )
        self.thread.start()
        self.logger.debug("Configuration files watchdog successfully started")

    def stop_watchdog(self) -> None:
        if not self.started:  # pragma: no cover
            raise RuntimeError("Configuration files watchdog is not running")

        self.logger.info("Stopping the configuration files watchdog")
        self._stop_event.set()
        self.thread.join()
        self.thread = None
        self.logger.debug("Configuration files watchdog successfully stopped")

    @contextmanager
    def config_files_lock_no_reset(self):
        with self._config_files_lock:
            yield

    @contextmanager
    def config_files_lock(self):
        """A context manager that makes sure only one process access file
        content at the time"""
        with self.config_files_lock_no_reset():
            try:
                yield
            finally:
                self._get_changed_config_files()

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
        sun_times = self.sun_times.get(place)
        if sun_times is None:
            return None
        if sun_times["last_update"] < date.today():
            del self._sun_times[place]
            return None
        return sun_times["data"]

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

    def _clean_sun_times_cache(
            self,
            sun_times_cache: dict[str, SunTimesCacheData],
    ) -> tuple[dict[str, SunTimesCacheData], bool]:
        outdated: list[str] = []
        today = date.today()
        for place in sun_times_cache:
            if sun_times_cache[place]["last_update"] < today:
                outdated.append(place)
        for place in outdated:
            self.logger.debug(f"Cached sun times of {place} is outdated.")
            del sun_times_cache[place]
        return sun_times_cache, bool(outdated)

    def _load_cached_sun_times(self) -> None:
        self.logger.debug("Loading cached sun times.")
        validated: dict[str, SunTimesCacheData] = {}
        try:
            file_path = self.get_file_path(CacheType.sun_times)
            with file_path.open("r") as file:
                unvalidated = json.loads(file.read())
                try:
                    validated: dict[str, SunTimesCacheData] = RootSunTimesCacheValidator(
                        **{"config": unvalidated}
                    ).model_dump()["config"]
                except ValidationError:
                    self.logger.debug("Cached sun times data out of format.")
                    os.remove(file_path)
        except (FileNotFoundError, JSONDecodeError, KeyError):
            self.logger.debug("No sun times cached.")

        cleaned_validated, any_outdated = self._clean_sun_times_cache(validated)
        self._sun_times = cleaned_validated
        if any_outdated:
            self.save(CacheType.sun_times)

    def _dump_sun_times(self) -> None:
        sun_times_path = self.get_file_path(CacheType.sun_times)
        with sun_times_path.open("w") as file:
            file.write(json.dumps(self._sun_times))

    def refresh_sun_times(self) -> None:
        self.logger.info("Looking if sun times need to be updated.")
        # Remove outdated data
        cleaned_validated, any_outdated = self._clean_sun_times_cache(self._sun_times)
        self._sun_times = cleaned_validated
        self.logger.debug("Found outdated sun times.")

        # Check if an update is required
        places: set[str] = set()
        # TODO: only do for running ecosystems ?
        for ecosystem_config in self.ecosystems_config_dict.values():
            sky = gv.SkyConfig(**ecosystem_config["environment"]["sky"])
            if sky.lighting == gv.LightMethod.elongate:
                # If we don't have an updated value, add "home" to the checklist
                if not self.home_sun_times:
                    places.add("home")
            elif sky.lighting == gv.LightMethod.mimic:
                target = sky.target
                # Check that we have the target coordinates. If we don't, log an
                #  error and use a fixed light method
                if not target:
                    ecosystem_name = ecosystem_config["name"]
                    self.logger.error(
                        f"Ecosystem '{ecosystem_name}' has no target set.")
                    ecosystem_config["environment"]["sky"]["lighting"] = gv.LightMethod.fixed
                    continue
                # If we don't have an updated value, add the target to the checklist
                if not self.get_sun_times(target):
                    places.add(target)
        if not places:
            self.logger.debug("No need to refresh sun times.")
            if any_outdated:
                self.save(CacheType.sun_times)
            return

        self.logger.info(
            f"Sun times of the following targets need to be refreshed: "
            f"{humanize_list(list(places))}."
        )
        any_failed = False
        any_success = False
        for place in places:
            sun_times = self.download_sun_times(place)
            if sun_times is not None:
                self.set_sun_times(place, sun_times)
                any_success = True
            else:
                any_failed = True

        if any_failed:
            self.logger.warning(
                "Could not refresh all sun times, some functionalities might not "
                "work as expected."
            )
        if any_outdated or any_success:
            self.save(CacheType.sun_times)

    def download_sun_times(self, target: str = "home") -> gv.SunTimesDict | None:
        self.logger.info(f"Trying to download sun times for the target '{target}'.")
        place = self.get_place(target)
        if place is None:
            self.logger.warning(
                f"You need to define '{target}' coordinates in "
                f"'private.cfg' in order to be able to download sun times."
            )
            return None
        try:
            self.logger.debug(
                f"Trying to update sunrise and sunset times for '{place}' "
                f"on sunrise-sunset.org."
            )
            with Session() as session:
                response = session.get(
                    url=f"https://api.sunrise-sunset.org/json",
                    params={
                        "lat": place.latitude,
                        "lng": place.longitude,
                    },
                    timeout=3.0,
                )
            data = response.json()
            try:
                results: gv.SunTimesDict = gv.SunTimes(
                    **data["results"]
                ).model_dump()
                return results
            except ValidationError:
                self.logger.error(
                    f"Could not validate sun times data for '{place}'.")
                return None
        except ConnectionError:
            self.logger.error(
                f"Failed to update sunrise and sunset times for '{place}' "
                f"due to a connection error."
            )
            return None

    def check_lighting_method_validity(
            self,
            ecosystem_uid: str,
            lighting_method: gv.LightMethod | None = None
    ) -> bool:
        ecosystem_name = self.get_IDs(ecosystem_uid).name
        sky_cfg: gv.SkyConfigDict = \
            self.ecosystems_config_dict[ecosystem_uid]["environment"]["sky"]
        lighting_method = lighting_method or sky_cfg["lighting"]
        lighting_method = safe_enum_from_name(gv.LightMethod, lighting_method)
        if lighting_method == gv.LightMethod.fixed:
            return True
        # Try to get the target
        elif lighting_method == gv.LightMethod.elongate:
            target = "home"
        elif lighting_method == gv.LightMethod.mimic:
            target = sky_cfg.get("target")
            if target is None:
                self.logger.warning(
                    f"Lighting method for ecosystem {ecosystem_name} cannot be "
                    f"'mimic' as no target is specified in the ecosystems "
                    f"configuration file."
                )
                return False
        else:
            raise ValueError("'lighting_method' should be a valid lighting method.")
        # Try to get the target's coordinates
        place = self.get_place(target)
        if place is None:
            self.logger.warning(
                f"Lighting method for ecosystem {ecosystem_name} cannot be "
                f"'{lighting_method.name}' as the coordinates of '{target}' is "
                f"not provided in the private configuration file."
            )
            return False
        # Try to get the target's sun times
        sun_times = self.get_sun_times(target)
        if sun_times:
            return True
        sun_times = self.download_sun_times(target)
        if sun_times is None:
            self.logger.warning(
                f"Lighting method for ecosystem {ecosystem_name} cannot be "
                f"'{lighting_method.name}' as the sun times of '{target}' "
                f"wasn't found."
            )
            return False
        self.set_sun_times(target, sun_times)
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
        self._lighting_hours = gv.LightingHours(
            morning_start=self.time_parameters.day,
            evening_end=self.time_parameters.night,
        )
        self.lighting_hours_lock = Lock()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.uid}, name={self.name}, " \
               f"engine_config={self._engine_config})"

    @property
    def __dict(self) -> EcosystemConfigDict:
        return self._engine_config.ecosystems_config_dict[self.uid]

    def as_dict(self) -> EcosystemConfigDict:
        return self.__dict

    def save(self) -> None:
        self._engine_config.save(ConfigType.ecosystems)

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

    def get_management(
            self,
            management: gv.ManagementNames | gv.ManagementFlags,
    ) -> bool:
        validated_management = safe_enum_from_name(gv.ManagementFlags, management)
        management_name: gv.ManagementNames = validated_management.name
        return self.__dict["management"].get(management_name, False)

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
    def sky(self) -> gv.SkyConfigDict:
        """
        Returns the sky config for the ecosystem
        """
        try:
            return self.environment["sky"]
        except KeyError:
            self.environment["sky"] = gv.SkyConfig().model_dump()
            return self.environment["sky"]

    @property
    def _light_method(self) -> gv.LightMethod:
        return safe_enum_from_name(gv.LightMethod, self.sky["lighting"])

    @property
    def light_method(self) -> gv.LightMethod:
        if self.general.app_config.TESTING:
            return self._light_method
        if self.sun_times is None:
            return gv.LightMethod.fixed
        return self._light_method

    @light_method.setter
    def light_method(self, light_method: gv.LightMethod) -> None:
        if not self.general.app_config.TESTING:
            raise AttributeError("can't set attribute 'light_method'")
        self.sky["lighting"] = light_method

    def set_light_method(self, method: gv.LightMethod) -> None:
        method = safe_enum_from_name(gv.LightMethod, method)
        method_is_valid = self.general.check_lighting_method_validity(self.uid, method)
        if not method_is_valid:
            raise ValueError(
                    f"Cannot set light method to '{method.name}'. Look at the "
                    f"logs to see the reason."
                )
        self.sky["lighting"] = method
        self.refresh_lighting_hours(send_info=True)

    @property
    def light_target(self) -> str | None:
        return self.sky["target"]

    def set_light_target(self, target: str | None) -> None:
        assert self.general.get_place(target)
        self.sky["target"] = target
        self.refresh_lighting_hours(send_info=True)

    @property
    def lighting_hours(self) -> gv.LightingHours:
        # TODO: reduce the use of this
        with self.lighting_hours_lock:
            return self._lighting_hours

    @lighting_hours.setter
    def lighting_hours(self, lighting_hours: gv.LightingHours) -> None:
        if not self.general.app_config.TESTING:
            raise AttributeError(
                "'lighting_hours' can only be set when 'TESTING' is True.")
        with self.lighting_hours_lock:
            self._lighting_hours = lighting_hours

    def refresh_lighting_hours(self, send_info: bool = True) -> None:
        self.logger.info("Refreshing lighting hours.")
        time_parameters = self.time_parameters
        # Check we've got the info required
        # Then update info using lock as the whole dict should be transformed at the "same time"
        # Compute for 'fixed' lighting method
        if self.light_method == gv.LightMethod.fixed:
            with self.lighting_hours_lock:
                self._lighting_hours = gv.LightingHours(
                    morning_start=time_parameters.day,
                    evening_end=time_parameters.night,
                )
        # Compute for 'mimic' lighting method
        elif self.light_method == gv.LightMethod.mimic:
            if self.sun_times is None:
                self.logger.warning(
                    "Cannot use lighting method 'mimic' without sun times "
                    "available. Using 'fixed' method instead."
                )
                self.set_light_method(gv.LightMethod.fixed)
                self.refresh_lighting_hours(send_info=send_info)
                return
            else:
                with self.lighting_hours_lock:
                    self._lighting_hours = gv.LightingHours(
                        morning_start=self.sun_times["sunrise"],
                        evening_end=self.sun_times["sunset"],
                    )
        # Compute for 'elongate' lighting method
        elif self.light_method == gv.LightMethod.elongate:
            if (
                    time_parameters.day is None
                    or time_parameters.night is None
                    or self.sun_times is None
            ):
                self.logger.warning(
                    "Cannot use lighting method 'elongate' without time "
                    "parameters set in config and sun times available. Using "
                    "'fixed' method instead."
                )
                self.set_light_method(gv.LightMethod.fixed)
                self.refresh_lighting_hours(send_info=send_info)
                return
            else:
                sunrise: datetime = _to_dt(self.sun_times["sunrise"])
                sunset: datetime = _to_dt(self.sun_times["sunset"])
                twilight_begin: datetime = _to_dt(self.sun_times["twilight_begin"])
                offset = sunrise - twilight_begin
                with self.lighting_hours_lock:
                    self._lighting_hours = gv.LightingHours(
                        morning_start=time_parameters.day,
                        morning_end=(sunrise + offset).time(),
                        evening_start=(sunset - offset).time(),
                        evening_end=time_parameters.night,
                    )
        if (
                send_info
                and self.general.engine_set_up
                and self.general.engine.use_message_broker
        ):
            try:
                self.general.engine.event_handler.send_payload_if_connected(
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
        chaos_memory = self.general.get_chaos_memory(self.uid)
        if chaos_memory["last_update"] < date.today():
            self._update_chaos_time_window()
        return self.general.get_chaos_memory(self.uid)["time_window"]

    def update_chaos_time_window(self, send_info: bool = True) -> None:
        self.logger.info("Updating chaos time window.")
        if self.general.get_chaos_memory(self.uid)["last_update"] < date.today():
            self._update_chaos_time_window()
            if (
                    send_info
                    and self.general.engine_set_up
                    and self.general.engine.use_message_broker
            ):
                self.general.engine.event_handler.send_payload_if_connected(
                    "chaos_parameters")
        else:
            self.logger.debug("Chaos time window is already up to date.")

    def _update_chaos_time_window(self) -> None:
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
        self.general.save(CacheType.chaos)

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
        Returns the sky config for the ecosystem
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

    """Parameters related to time"""
    @property
    def time_parameters(self) -> gv.DayConfig:
        return gv.DayConfig(
            day=self.sky["day"],
            night=self.sky["night"],
        )

    @time_parameters.setter
    def time_parameters(self, value: gv.DayConfigDict) -> None:
        """Set time parameters

        :param value: A dict in the form {'day': '8h00', 'night': '22h00'}
        """
        try:
            validated_value = gv.DayConfig(**value).model_dump()
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid time parameters provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`."
            )
        self.environment["sky"].update(validated_value)

    @property
    def sun_times(self) -> gv.SunTimesDict | None:
        if self._light_method == gv.LightMethod.mimic:
            target = self.light_target
            sun_times = self.general.get_sun_times(target)
            return sun_times
        return self.general.home_sun_times


# ---------------------------------------------------------------------------
#   Functions to interact with the module
# ---------------------------------------------------------------------------
def get_IDs(ecosystem: str) -> gv.IDs:
    """Return the tuple (ecosystem_uid, ecosystem_name)

    :param ecosystem: str, either an ecosystem uid or ecosystem name
    """
    return EngineConfig().get_IDs(ecosystem)
