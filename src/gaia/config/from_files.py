from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from json.decoder import JSONDecodeError
import logging
import os
from math import pi, sin
from pathlib import Path
import random
import string
from threading import Condition, Event, Lock, Thread
import typing as t
from typing import cast, Literal, Type, TypedDict
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
from gaia.utils import json, SingletonMeta, yaml


if t.TYPE_CHECKING:
    from gaia.engine import Engine


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


class CoordinatesValidator(gv.BaseModel):
    latitude: float
    longitude: float


class CoordinatesDict(TypedDict):
    latitude: float
    longitude: float


class PlaceValidator(gv.BaseModel):
    name: str
    coordinates: CoordinatesValidator


class PlaceDict(TypedDict):
    name: str
    coordinates: CoordinatesDict


# ---------------------------------------------------------------------------
#   Ecosystem config models
# ---------------------------------------------------------------------------
# Custom models for Climate and Environment configs as some of their
#  parameters are used as keys in ecosystems.cfg
class EnvironmentConfigValidator(gv.BaseModel):
    chaos: gv.ChaosConfig = Field(default_factory=gv.ChaosConfig)
    sky: gv.SkyConfig = Field(default_factory=gv.SkyConfig)
    climate: dict[gv.ClimateParameterNames, gv.AnonymousClimateConfig] = \
        Field(default_factory=dict)

    @field_validator("climate", mode="before")
    def dict_to_climate(cls, value: dict):
        return {k: gv.AnonymousClimateConfig(**v) for k, v in value.items()}


class EnvironmentConfigDict(TypedDict):
    chaos: gv.ChaosConfigDict
    sky: gv.SkyConfigDict
    climate: dict[str, gv.AnonymousClimateConfigDict]


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
class ChaosTimeWindowValidator(gv.BaseModel):
    beginning: datetime | None = None
    end: datetime | None = None

    @field_validator("beginning", "end", mode="before")
    def parse_time(cls, value):
        if isinstance(value, str):
            dt = datetime.fromisoformat(value)
            dt.astimezone(timezone.utc)
            return dt
        return value


class ChaosTimeWindow(TypedDict):
    beginning: datetime | None
    end: datetime | None


class ChaosMemoryValidator(gv.BaseModel):
    last_update: date = Field(default_factory=date.today)
    time_window: ChaosTimeWindowValidator = ChaosTimeWindowValidator()

    @field_validator("last_update", mode="before")
    def parse_last_update(cls, value):
        if isinstance(value, str):
            return date.fromisoformat(value)
        return value


class ChaosMemory(TypedDict):
    last_update: date
    time_window: ChaosTimeWindow


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
        self._private_config: dict = {}
        self._sun_times: [str, SunTimesCacheData] = {}
        self._chaos_memory: dict[str, ChaosMemory] = {}
        # Watchdog threading securities
        self._config_files_modif: dict[Path, int] = {}
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
                    validated = RootEcosystemsConfigValidator(
                        **{"config": unvalidated}
                    ).model_dump()["config"]
                except pydantic.ValidationError as e:
                    self.logger.error(
                        f"Could not validate ecosystems configuration file. "
                        f"ERROR msg(s): `{format_pydantic_error(e)}`."
                    )
                    raise e
                else:
                    self._ecosystems_config_dict = validated
        elif cfg_type == ConfigType.private:
            with open(config_path, "r") as file:
                self._private_config = yaml.load(file)

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
        self._private_config = {}
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
                    path = self.get_file_path(cfg_type)
                    self._config_files_modif[path] = os.stat(path).st_mtime_ns
        self.load(CacheType.chaos)
        self.load(CacheType.sun_times)
        for ecosystem_uid, eco_cfg_dict in self.ecosystems_config_dict.items():
            ecosystem_name = self.get_IDs(ecosystem_uid).name
            self.logger.info(
                f"Checking if light method for ecosystem {ecosystem_name} is valid.")
            light_is_method_valid = self.check_lighting_method_validity(ecosystem_uid)
            if not light_is_method_valid:
                eco_cfg_dict["environment"]["sky"]["lighting"] = gv.LightMethod.fixed
        self.configs_loaded = True

    def save(self, cfg_type: ConfigType | CacheType) -> None:
        if isinstance(cfg_type, ConfigType):
            with self.config_files_lock():
                self.logger.debug(f"Updating {cfg_type.name} configuration file(s)")
                self._dump_config(cfg_type)
        else:
            if cfg_type == CacheType.chaos:
                self._dump_chaos_memory()
            elif cfg_type == CacheType.sun_times:
                self._dump_sun_times()

    def load(self, cfg_type: ConfigType | CacheType) -> None:
        if isinstance(cfg_type, ConfigType):
            with self.config_files_lock():
                self.logger.debug(f"Loading {cfg_type.name} configuration file(s)")
                self._load_config(cfg_type)
        else:
            if cfg_type == CacheType.chaos:
                self._load_chaos_memory()
            elif cfg_type == CacheType.sun_times:
                self._load_cached_sun_times()

    # File watchdog
    def _get_changed_config_files(self) -> set[ConfigType]:
        config_files_mtime: dict[Path, int] = {}
        changed: set[ConfigType] = set()
        for file_path, file_modif in self._config_files_modif.items():
            modif = os.stat(file_path).st_mtime_ns
            if modif != file_modif:
                changed.add(ConfigType(file_path.name))
            config_files_mtime[file_path] = modif
        self._config_files_modif = config_files_mtime
        return changed

    def _watchdog_routine(self) -> None:
        # Fill config files modification dict
        with self.config_files_lock():
            changed_configs = self._get_changed_config_files()
            if changed_configs:
                for config_type in changed_configs:
                    self.logger.info(
                        f"Change in '{config_type.value}' detected. Updating "
                        f"{config_type.name} configuration.")
                    self._load_config(cfg_type=config_type)
                    if config_type is ConfigType.ecosystems:
                        self.refresh_sun_times()
                with self.new_config:
                    self.new_config.notify_all()
                if self.engine_set_up and self.engine.use_message_broker:
                    self.engine.event_handler.send_ecosystems_info()

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
    def config_files_lock(self):
        """A context manager that makes sure only one process access file
        content at the time"""
        with self._config_files_lock:
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
        if self.app_config.TESTING:
            self._ecosystems_config_dict = value
        else:
            raise AttributeError("Can't set attribute 'ecosystems_config_dict'")

    @property
    def private_config(self) -> dict:
        return self._private_config

    @private_config.setter
    def private_config(self, value: dict):
        if self.app_config.TESTING:
            self._private_config = value
        else:
            raise AttributeError("can't set attribute 'private_config'")

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
            f"Ecosystem with id '{ecosystem_id}' not found.'ecosystem_id' parameter "
            f"should either be an ecosystem uid or an ecosystem name present in "
            f"the 'ecosystems.cfg' file. If you want to create a new ecosystem "
            f"configuration use the function `create_ecosystem()`."
        )

    """Private config parameters"""
    @property
    def places(self) -> dict[str, CoordinatesDict]:
        try:
            return self._private_config["places"]
        except KeyError:
            self._private_config["places"] = {}
            return self._private_config["places"]

    def get_place(self, place: str) -> PlaceValidator:
        try:
            coordinates: CoordinatesDict = self.places[place]
            return PlaceValidator(name=place, coordinates=coordinates)
        except KeyError:
            raise UndefinedParameter(
                f"No place named '{place}' was found in the private "
                f"configuration file")

    def set_place(
            self,
            place: str,
            coordinates: tuple[float, float] | CoordinatesDict
    ) -> None:
        if isinstance(coordinates, tuple):
            coordinates = CoordinatesDict(
                latitude=coordinates[0],
                longitude=coordinates[1]
            )
        validated_coordinates: CoordinatesDict = CoordinatesValidator(
            **coordinates).model_dump()
        self.places[place] = validated_coordinates

    def CRUD_create_place(self, value: PlaceDict):
        validated_value: PlaceDict = PlaceValidator(**value).model_dump()
        place = validated_value.pop("name")
        self.places[place] = validated_value["coordinates"]

    def CRUD_update_place(self, value: PlaceDict) -> None:
        validated_value: PlaceDict = PlaceValidator(**value).model_dump()
        place = validated_value.pop("name")
        if place not in self.places:
            raise UndefinedParameter(
                f"No place named '{place}' was found in the private "
                f"configuration file")
        self.places[place] = validated_value["coordinates"]

    @property
    def home(self) -> PlaceValidator:
        return self.get_place("home")

    @property
    def home_coordinates(self) -> CoordinatesValidator:
        return self.home.coordinates

    @home_coordinates.setter
    def home_coordinates(self, value: tuple[float, float] | CoordinatesDict) -> None:
        self.set_place("home", coordinates=value)

    @property
    def units(self) -> dict[str, str]:
        return self._private_config.get("units", {})

    @property
    def sun_times(self) -> dict[str, SunTimesCacheData]:
        return self._sun_times

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
            if sun_times_cache[place]["last_update"] <= today:
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
        # Remove outdated data
        cleaned_validated, any_outdated = self._clean_sun_times_cache(self._sun_times)
        self._sun_times = cleaned_validated

        # Check if an update is required
        places: set[str] = set()
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

    def download_sun_times(self, place: str) -> gv.SunTimesDict | None:
        self.logger.info("Trying to download sun times")
        try:
            coordinates = self.get_place(place).coordinates
        except UndefinedParameter:
            self.logger.warning(
                f"You need to define '{place}' coordinates in "
                f"'private.cfg' in order to download sun times."
            )
            return None
        else:
            try:
                self.logger.debug(
                    f"Trying to update sunrise and sunset times for '{place}' "
                    f"on sunrise-sunset.org."
                )
                with Session() as session:
                    response = session.get(
                        url=f"https://api.sunrise-sunset.org/json",
                        params={
                            "lat": coordinates.latitude,
                            "lng": coordinates.longitude
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
            return  True
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
        try:
            self.get_place(target)
        except UndefinedParameter:
            self.logger.warning(
                f"Lighting method for ecosystem {ecosystem_name} cannot be "
                f"'{lighting_method.name}' as the coordinates of '{target}' is "
                f"provided in the private configuration file."
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
                f"config"
            )
        if ecosystem_uid not in self._chaos_memory:
            self._chaos_memory.update(self._create_chaos_memory(ecosystem_uid))
        return self._chaos_memory[ecosystem_uid]

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
                "Configuration files need to be loaded by `EngineConfig` in"
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
        self.logger.debug(f"Initializing EcosystemConfig for {ids.name}")

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
        if self.sun_times is None:
            return gv.LightMethod.fixed
        return self._light_method

    def set_light_method(self, method: gv.LightMethod) -> None:
        method = safe_enum_from_name(gv.LightMethod, method)
        method_is_valid = self.general.check_lighting_method_validity(self.uid, method)
        if not method_is_valid:
            raise ValueError(
                    f"Cannot set light method to '{method.name}'. Look at the "
                    f"logs to see the reason."
                )
        self.sky["lighting"] = method

    @property
    def light_target(self) -> str | None:
        return self.sky["target"]

    def set_light_target(self, target: str | None) -> None:
        assert self.general.get_place(target)
        self.sky["target"] = target

    @property
    def chaos_parameters(self) -> gv.ChaosConfig:
        try:
            return gv.ChaosConfig(**self.environment["chaos"])
        except KeyError:
            raise UndefinedParameter(f"Chaos as not been set in {self.name}")

    @chaos_parameters.setter
    def chaos_parameters(self, values: gv.ChaosConfigDict) -> None:
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
    def chaos_time_window(self) -> ChaosTimeWindow:
        chaos_memory = self.general.get_chaos_memory(self.uid)
        if chaos_memory["last_update"] < date.today():
            self._update_chaos_time_window()
        return self.general.get_chaos_memory(self.uid)["time_window"]

    def update_chaos_time_window(self) -> None:
        self.logger.info("Updating chaos time window.")
        if self.general.get_chaos_memory(self.uid)["last_update"] < date.today():
            self._update_chaos_time_window()
        else:
            self.logger.debug("Chaos time window is already up to date.")

    def _update_chaos_time_window(self) -> None:
        chaos_memory = self.general.get_chaos_memory(self.uid)
        beginning = chaos_memory["time_window"]["beginning"]
        end = chaos_memory["time_window"]["end"]
        if beginning and end:
            if not (beginning <= date.today() <= end):  # End of chaos period
                beginning = None
                end = None
        else:
            if self.chaos_parameters.frequency:
                chaos_probability = random.randint(1, self.chaos_parameters.frequency)
            else:
                chaos_probability = 0
            if chaos_probability == 1:
                today = datetime.now(timezone.utc).replace(
                    hour=14, minute=0, second=0, microsecond=0)
                beginning = today
                end = today + timedelta(days=self.chaos_parameters.duration)
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
        return (sin(chaos_radian) * (self.chaos_parameters.intensity - 1.0)) + 1.0

    @property
    def climate(self) -> dict[gv.ClimateParameterNames, gv.AnonymousClimateConfigDict]:
        """
        Returns the sky config for the ecosystem
        """
        try:
            return self.environment["climate"]
        except KeyError:
            self.environment["climate"] = {}
            return self.environment["climate"]

    def get_climate_parameter(self, parameter: gv.ClimateParameterNames) -> gv.ClimateConfig:
        try:
            data = self.climate[parameter]
            return gv.ClimateConfig(parameter=parameter, **data)
        except KeyError:
            raise UndefinedParameter(
                f"No climate parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file")

    def set_climate_parameter(
            self,
            parameter: gv.ClimateParameterNames,
            value: gv.AnonymousClimateConfigDict
    ) -> None:
        try:
            validated_value = gv.AnonymousClimateConfig(**value).model_dump()
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid climate parameters provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`."
            )
        self.climate[parameter] = validated_value

    def delete_climate_parameter(
            self,
            parameter: gv.ClimateParameterNames,
    ) -> None:
        try:
            del self.climate[parameter]
        except KeyError:
            raise UndefinedParameter(
                f"No climate parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file")

    def CRUD_create_climate_parameter(self, value: gv.AnonymousClimateConfigDict) -> None:
        validated_value: gv.ClimateConfigDict = gv.ClimateConfig(**value).model_dump()
        parameter = validated_value.pop("parameter")
        self.climate[parameter] = validated_value

    def CRUD_update_climate_parameter(self, value: gv.AnonymousClimateConfigDict) -> None:
        validated_value: gv.ClimateConfigDict = gv.ClimateConfig(**value).model_dump()
        parameter = validated_value.pop("parameter")
        if parameter not in self.climate:
            raise UndefinedParameter(
                f"No climate parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file")
        self.climate[parameter] = validated_value

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

    def _validate_hardware_dict(
            self,
            hardware_dict: gv.HardwareConfigDict,
            check_address: bool = True,
    ) -> Hardware:
        try:
            hardware_config = gv.HardwareConfig(**hardware_dict)
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid hardware information provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`"
            )
        if (
                check_address
                and hardware_config.address in self._used_addresses()
        ):
            raise ValueError(f"Address {hardware_config.address} already used.")
        if hardware_config.model not in hardware_models:
            raise ValueError(
                "This hardware model is not supported. Use "
                "'EcosystemConfig.supported_hardware()' to see supported hardware."
            )
        hardware_cls = hardware_models[hardware_config.model]
        return hardware_cls.from_hardware_config(hardware_config, None)

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
        hardware = self._validate_hardware_dict(hardware_dict)
        hardware_repr = hardware.dict_repr(shorten=True)
        hardware_repr.pop("uid")
        self.IO_dict.update({uid: hardware_repr})

    def CRUD_create_hardware(self, value: gv.HardwareConfigDict) -> None:
        self.create_new_hardware(**value)

    def update_hardware(self, uid: str, update_value: dict) -> None:
        try:
            non_null_values = {
                key: value for key, value in update_value.items()
                if value is not None
            }
            hardware_dict: gv.AnonymousHardwareConfigDict = self.IO_dict[uid].copy()
            hardware_dict: gv.HardwareConfigDict = cast(gv.HardwareConfigDict, hardware_dict)
            hardware_dict.update({"uid": uid, **non_null_values})
            check_address = "address" in update_value  # Don't check address if not trying to update it
            hardware = self._validate_hardware_dict(hardware_dict, check_address)
            hardware_repr = hardware.dict_repr(shorten=True)
            hardware_repr.pop("uid")
            self.IO_dict[uid] = hardware_repr
        except KeyError:
            raise HardwareNotFound

    def CRUD_update_hardware(self, value: gv.HardwareConfigDict) -> None:
        validated_value: gv.HardwareConfigDict = gv.HardwareConfig(
            **value).model_dump()
        uid = validated_value.pop("uid")
        if uid not in self.IO_dict:
            raise HardwareNotFound
        self.IO_dict[uid] = validated_value

    def delete_hardware(self, uid: str) -> None:
        """
        Delete a hardware from the config
        :param uid: str, the uid of the hardware to delete
        """
        try:
            del self.IO_dict[uid]
        except KeyError:
            raise HardwareNotFound

    def get_hardware_uid(self, name: str) -> str:
        for uid, hardware in self.IO_dict.items():
            if hardware["name"] == name:
                return uid
        raise HardwareNotFound

    def get_hardware_config(self, uid: str) -> gv.HardwareConfig:
        try:
            hardware_config = self.IO_dict[uid]
            return gv.HardwareConfig(uid=uid, **hardware_config)
        except KeyError:
            raise HardwareNotFound

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
            if sun_times is not None:
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
