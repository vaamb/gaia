from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time
from enum import Enum
import hashlib
from json.decoder import JSONDecodeError
import logging
import pathlib
import random
from requests import ConnectionError, Session
import string
from threading import Condition, Event, Lock, Thread
import typing as t
from typing import Literal, TypedDict
import weakref

import gaia_validators as gv
from gaia_validators import safe_enum_from_name

# TODO: move up once compatibility issues are solved
from pydantic import Field, field_validator, ValidationError  # noqa

from gaia.config._utils import (
    get_base_dir, get_cache_dir, get_config as get_gaia_config)
from gaia.exceptions import (
    EcosystemNotFound, HardwareNotFound, UndefinedParameter)
from gaia.hardware import hardware_models
from gaia.subroutines import subroutines
from gaia.utils import json, SingletonMeta, utc_time_to_local_time, yaml


if t.TYPE_CHECKING:
    from gaia.engine import Engine


logger = logging.getLogger("gaia.config.environments")

config_condition = Condition()


class ConfigType(Enum):
    ecosystems = "_ecosystems_config"
    private = "_private_config"


def _file_hash(file_path: pathlib.Path) -> str:
    try:
        h = hashlib.md5(usedforsecurity=False)
        with open(file_path, "rb") as f:
            for block in iter(lambda: f.read(4096), b""):
                h.update(block)
        return h.hexdigest()
    except FileNotFoundError:
        return "0x0"


# ---------------------------------------------------------------------------
#   Common config models
# ---------------------------------------------------------------------------
DaytimeEvents = Literal[
    "civil_twilight_begin", "sunrise", "sunset", "civil_twilight_end"]


class SunTimesCacheHomeDict(TypedDict):
    home: gv.SunTimesDict


class SunTimesCacheDict(TypedDict):
    last_update: str
    data: SunTimesCacheHomeDict


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
#   EngineConfig class
# ---------------------------------------------------------------------------
class EngineConfig(metaclass=SingletonMeta):
    """Class to interact with the configuration files

    To interact with a specific ecosystem configuration, the EcosystemConfig
    class should be used.
    """
    def __init__(self, base_dir=get_base_dir()) -> None:
        logger.debug("Initializing EngineConfig")
        self._base_dir = pathlib.Path(base_dir)
        self._engine: "Engine" | None = None
        self._ecosystems_config: dict = {}
        self._private_config: dict = {}
        self._sun_times: gv.SunTimes | None = None
        # Watchdog threading securities
        self._hash_dict: dict[ConfigType, str] = {}
        self._config_files_lock = Lock()
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
        self._engine = value

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
        config_path = self._base_dir/f"{cfg_type.name}.cfg"
        if cfg_type == ConfigType.ecosystems:
            with open(config_path, "r") as file:
                unvalidated = yaml.load(file)
                try:
                    validated = RootEcosystemsConfigValidator(
                        **{"config": unvalidated}
                    ).model_dump()["config"]
                except ValidationError as e:
                    # TODO: log formatted error message
                    raise e
                else:
                    self._ecosystems_config = validated
        elif cfg_type == ConfigType.private:
            with open(config_path, "r") as file:
                self._private_config = yaml.load(file)

    def _dump_config(self, cfg_type: ConfigType):
        # /!\ must be used with the config_files_lock acquired
        self._check_files_lock_acquired()
        # TODO: shorten dicts used ?
        config_path = self._base_dir/f"{cfg_type.name}.cfg"
        with open(config_path, "w") as file:
            cfg = getattr(self, cfg_type.value)
            yaml.dump(cfg, file)

    def _create_ecosystems_config_file(self):
        self._ecosystems_config = {}
        self._create_ecosystem("Default Ecosystem")
        self._dump_config(ConfigType.ecosystems)

    def _create_private_config_file(self):
        self._private_config = {}
        self._dump_config(ConfigType.private)

    def initialize_configs(self) -> None:
        with self.config_files_lock():
            for cfg_type in ConfigType:
                try:
                    self._load_config(cfg_type)
                except OSError:
                    if cfg_type == ConfigType.ecosystems:
                        logger.warning(
                            "No custom `ecosystems.cfg` configuration file "
                            "detected. Creating a default file.")
                        self._create_ecosystems_config_file()
                    elif cfg_type == ConfigType.private:
                        logger.warning(
                            "No custom `private.cfg` configuration file "
                            "detected. Creating a default file.")
                        self._create_private_config_file()
        self.configs_loaded = True

    def save(self, cfg_type: ConfigType) -> None:
        with self.config_files_lock():
            logger.debug(f"Updating {cfg_type.name} configuration file(s)")
            self._dump_config(cfg_type)

    # File watchdog
    def _update_cfg_hash(self) -> None:
        for cfg_type in ConfigType:
            path = self._base_dir/f"{cfg_type.name}.cfg"
            self._hash_dict[cfg_type] = _file_hash(path)

    def _watchdog_loop(self) -> None:
        while not self._stop_event.is_set():
            reloaded = False
            with self.config_files_lock():
                old_hash = {**self._hash_dict}
                self._update_cfg_hash()
                # Need to reload the config if its hash has changed
                reload_cfg: list[ConfigType] = [
                    cfg_type for cfg_type in ConfigType
                    if old_hash[cfg_type] != self._hash_dict[cfg_type]
                ]
                if reload_cfg:
                    logger.info(
                        f"Change in config file(s) detected. Updating "
                        f"configuration file(s) {[cfg.name for cfg in reload_cfg]}")
                    for cfg in reload_cfg:
                        self._load_config(cfg_type=cfg)
                    with config_condition:
                        config_condition.notify_all()
                    reloaded = True
            if reloaded:
                if "ecosystems" in reload_cfg:
                    self.refresh_sun_times()
                if self.engine.use_message_broker:
                    self.engine.event_handler.send_full_config()
            self._stop_event.wait(get_gaia_config().CONFIG_WATCHER_PERIOD)

    def start_watchdog(self) -> None:
        if not self.configs_loaded:  # pragma: no cover
            raise RuntimeError(
                "Configuration files need to be loaded in order to start "
                "the config file watchdog. To do so, use the "
                "`EngineConfig().initialize_configs()` method."
            )

        if self.started:  # pragma: no cover
            raise RuntimeError("Configuration files watchdog is already running")

        logger.info("Starting the configuration files watchdog")
        self._update_cfg_hash()
        self.thread = Thread(
            target=self._watchdog_loop,
            name="config_watchdog")
        self.thread.start()
        logger.debug("Configuration files watchdog successfully started")

    def stop_watchdog(self) -> None:
        if not self.started:  # pragma: no cover
            raise RuntimeError("Configuration files watchdog is not running")

        logger.info("Stopping the configuration files watchdog")
        self._stop_event.set()
        self.thread.join()
        self.thread = None
        logger.debug("Configuration files watchdog successfully stopped")

    @contextmanager
    def config_files_lock(self):
        """A context manager that makes sure only one process access file
        content at the time"""
        with self._config_files_lock:
            try:
                yield
            finally:
                self._update_cfg_hash()

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
        self._ecosystems_config.update({uid: ecosystem_cfg})

    def create_ecosystem(self, ecosystem_name: str) -> None:
        self._create_ecosystem(ecosystem_name)

    def delete_ecosystem(self, ecosystem_id: str) -> None:
        ecosystem_ids = self.get_IDs(ecosystem_id)
        del self._ecosystems_config[ecosystem_ids.uid]

    @property
    def ecosystems_config(self) -> dict[str, EcosystemConfigDict]:
        return self._ecosystems_config

    @ecosystems_config.setter
    def ecosystems_config(self, value: dict):
        if get_gaia_config().TESTING:
            self._ecosystems_config = value
        else:
            raise AttributeError("can't set attribute 'ecosystems_config'")

    @property
    def private_config(self) -> dict:
        return self._private_config

    @private_config.setter
    def private_config(self, value: dict):
        if get_gaia_config().TESTING:
            self._private_config = value
        else:
            raise AttributeError("can't set attribute 'private_config'")

    @property
    def base_dir(self) -> pathlib.Path:
        return self._base_dir

    @property
    def ecosystems_uid(self) -> list[str]:
        return [i for i in self._ecosystems_config.keys()]

    @property
    def ecosystems_name(self) -> list:
        return [i["name"] for i in self._ecosystems_config.values()]

    @property
    def id_to_name_dict(self) -> dict:
        return {ecosystem: self._ecosystems_config[ecosystem]["name"]
                for ecosystem in self._ecosystems_config}

    @property
    def name_to_id_dict(self) -> dict:
        return {self._ecosystems_config[ecosystem]["name"]: ecosystem
                for ecosystem in self._ecosystems_config}

    def get_ecosystems_expected_to_run(self) -> set:
        return set([
            ecosystem_uid for ecosystem_uid in self._ecosystems_config
            if self._ecosystems_config[ecosystem_uid]["status"]
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
            "'ecosystem_id' parameter should either be an ecosystem uid or an "
            "ecosystem name present in the 'ecosystems.cfg' file. If you want "
            "to create a new ecosystem configuration use the function "
            "`create_ecosystem()`."
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

    @home.setter
    def home(self, coordinates: tuple[float, float] | CoordinatesDict) -> None:
        self.set_place("home", coordinates=coordinates)

    @property
    def home_name(self) -> str:
        return self.home.name

    @property
    def home_coordinates(self) -> CoordinatesValidator:
        return self.home.coordinates

    @property
    def units(self) -> dict[str, str]:
        return self._private_config.get("units", {})

    @property
    def sun_times(self) -> gv.SunTimes | None:
        return self._sun_times

    def refresh_sun_times(self) -> None:
        needed = False
        for ecosystem_config in self._ecosystems_config.values():
            sky = gv.SkyConfig(**ecosystem_config["environment"]["sky"])
            if sky.lighting != gv.LightMethod.fixed:
                needed = True
                break
        if not needed:
            logger.debug("No need to refresh sun times")
            return
        sun_times_file = get_cache_dir()/"sunrise.json"
        # Determine if the file needs to be updated
        sun_times_data: gv.SunTimesDict | None = None
        logger.debug("Trying to load cached sun times")
        try:
            with sun_times_file.open("r") as file:
                payload: SunTimesCacheDict = json.loads(file.read())
                last_update: datetime = \
                    datetime.fromisoformat(payload["last_update"]).astimezone()
        except (FileNotFoundError, JSONDecodeError, KeyError):
            pass
        else:
            if last_update.date() >= date.today():
                sun_times_data = payload["data"]["home"]
                logger.info("Sun times already up to date")
        if sun_times_data is None:
            sun_times_data = self.download_sun_times()

        if sun_times_data is not None:
            def import_daytime_event(daytime_event: DaytimeEvents) -> time:
                try:
                    my_time = datetime.strptime(
                        sun_times_data[daytime_event], "%I:%M:%S %p").astimezone().time()
                    local_time = utc_time_to_local_time(my_time)
                    return local_time
                except Exception:
                    raise UndefinedParameter(
                        f"Could not find {daytime_event} in sun times file.")

            self._sun_times = gv.SunTimes(
                twilight_begin=import_daytime_event("civil_twilight_begin"),
                sunrise=import_daytime_event("sunrise"),
                sunset=import_daytime_event("sunset"),
                twilight_end=import_daytime_event("civil_twilight_end"),
            )

        else:
            logger.warning(
                "Could not refresh sun times, some functionalities might not "
                "work as expected. All 'light_method's were set to 'fixed'."
            )
            self._sun_times = None

    def download_sun_times(self) -> gv.SunTimesDict | None:
        sun_times_file = get_cache_dir()/"sunrise.json"
        logger.info("Trying to download sun times")
        try:
            home_coordinates = self.home_coordinates
        except UndefinedParameter:
            logger.warning(
                "You need to define your home city coordinates in "
                "'private.cfg' in order to download sun times."
            )
            return None
        else:
            try:
                logger.debug(
                    "Trying to update sunrise and sunset times on "
                    "sunrise-sunset.org"
                )
                with Session() as session:
                    response = session.get(
                        url=f"https://api.sunrise-sunset.org/json",
                        params={
                            "lat": home_coordinates.latitude,
                            "lng": home_coordinates.longitude
                        },
                        timeout=3.0,
                    )
                data = response.json()
                results: gv.SunTimesDict = data["results"]
            except ConnectionError:
                logger.debug(
                    "Failed to update sunrise and sunset times due to a "
                    "connection error"
                )
                return None
            else:
                payload: SunTimesCacheDict = {
                    "last_update": datetime.now().astimezone().isoformat(),
                    "data": {"home": results},
                }
                with open(sun_times_file, "w") as file:
                    file.write(json.dumps(payload))
                logger.info(
                    "Sunrise and sunset times successfully updated")
                return results

    @staticmethod
    def get_ecosystem_config(ecosystem: str) -> "EcosystemConfig":
        return EcosystemConfig(ecosystem=ecosystem)


# ---------------------------------------------------------------------------
#   EcosystemConfig class
# ---------------------------------------------------------------------------
class _MetaEcosystemConfig(type):
    instances: dict[str, "EcosystemConfig"] = {}

    def __call__(cls, *args, **kwargs) -> "EcosystemConfig":
        if len(args) > 0:
            ecosystem = args[0]
        else:
            ecosystem = kwargs["ecosystem"]
        general_config = EngineConfig()
        if not general_config.configs_loaded:
            raise RuntimeError(
                "Configuration files need to be loaded by `EngineConfig` in"
                "order to get an `EcosystemConfig` instance. To do so, use the "
                "`EngineConfig().initialize_configs()` method."
            )
        ecosystem_uid =  general_config.get_IDs(ecosystem).uid
        try:
            return cls.instances[ecosystem_uid]
        except KeyError:
            ecosystem_config: EcosystemConfig = \
                cls.__new__(cls, ecosystem, *args, **kwargs)
            ecosystem_config.__init__(*args, **kwargs)
            cls.instances[ecosystem_uid] = ecosystem_config
            return ecosystem_config


class EcosystemConfig(metaclass=_MetaEcosystemConfig):
    def __init__(self, ecosystem: str) -> None:
        self._engine_config: EngineConfig = weakref.proxy(EngineConfig())
        ids = self._engine_config.get_IDs(ecosystem)
        self.uid = ids.uid
        self.logger = logging.getLogger(f"gaia.engine.{ids.name}.config")
        self.logger.debug(f"Initializing EcosystemConfig for {ids.name}")

    def __del__(self):
        try:
            del _MetaEcosystemConfig.instances[self.uid]
        except KeyError:  # already removed or failed to init
            pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.uid}, name={self.name}, " \
               f"engine_config={self._engine_config})"

    @property
    def __dict(self) -> EcosystemConfigDict:
        return self._engine_config.ecosystems_config[self.uid]

    def as_dict(self) -> EcosystemConfigDict:
        return self.__dict

    def save(self) -> None:
        if not get_gaia_config().TESTING:
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

    def get_management(self, management: gv.ManagementNames) -> bool:
        try:
            return self.__dict["management"].get(management, False)
        except (KeyError, AttributeError):  # pragma: no cover
            return False

    def set_management(self, management: gv.ManagementNames, value: bool) -> None:
        validated_management = safe_enum_from_name(gv.ManagementFlags, management)
        management_name: gv.ManagementNames = validated_management.name
        self.__dict["management"][management_name] = value

    def get_managed_subroutines(self) -> list[gv.ManagementNames]:
        return [subroutine for subroutine in subroutines
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
    def light_method(self) -> gv.LightMethod:
        if self.sun_times is None:
            return gv.LightMethod.fixed
        return safe_enum_from_name(gv.LightMethod, self.sky["lighting"])

    def set_light_method(self, method: gv.LightMethod) -> None:
        try:
            validated_method = safe_enum_from_name(gv.LightMethod, method)
        except KeyError:
            raise ValueError("'method' is not a valid 'LightMethod'")
        self.sky["lighting"] = validated_method
        if validated_method != gv.LightMethod.fixed:
            self.general.refresh_sun_times()

    @property
    def chaos(self) -> gv.ChaosConfig:
        try:
            return gv.ChaosConfig(**self.environment["chaos"])
        except KeyError:
            raise UndefinedParameter(f"Chaos as not been set in {self.name}")

    @chaos.setter
    def chaos(self, values: gv.ChaosConfigDict) -> None:
        """Set chaos parameter

        :param values: A dict with the entries 'frequency': int,
                       'duration': int and 'intensity': float.
        """
        validated_values = gv.ChaosConfig(**values).model_dump()
        self.environment["chaos"] = validated_values

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
        validated_value = gv.AnonymousClimateConfig(**value).model_dump()
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
            IO_type: str,
            level: tuple = ("environment", "plants")
    ) -> list[str]:
        return [uid for uid in self.IO_dict
                if self.IO_dict[uid]["type"].lower() == IO_type
                and self.IO_dict[uid]["level"].lower() in level]

    def _create_new_IO_uid(self) -> str:
        length = 16
        used_ids = list(self.IO_dict.keys())
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
        if address in self._used_addresses():
            raise ValueError(f"Address {address} already used")
        if model not in hardware_models:
            raise ValueError(
                "This hardware model is not supported. Use "
                "'EcosystemConfig.supported_hardware()' to see supported hardware"
            )
        uid = self._create_new_IO_uid()
        h = hardware_models[model]
        hardware_config = gv.HardwareConfig(
            uid=uid,
            name=name,
            address=address,
            type=type,
            level=level,
            model=model,
            measures=measures,
            plants=plants,
            multiplexer_model=multiplexer_model,
        )
        new_hardware = h.from_hardware_config(hardware_config, None)
        hardware_repr = new_hardware.dict_repr(shorten=True)
        hardware_repr.pop("uid")
        self.IO_dict.update({uid: hardware_repr})

    def CRUD_create_hardware(self, value: gv.HardwareConfigDict) -> None:
        self.create_new_hardware(**value)

    def update_hardware(self, uid: str, update_value: dict) -> None:
        try:
            non_null_value = {
                key: value for key, value in update_value.items()
                if value is not None
            }
            base = self.IO_dict[uid].copy()
            base.update(non_null_value)
            validated_value = gv.HardwareConfig(uid=uid, **base).model_dump()
            self.IO_dict[uid].update(**validated_value)
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
        validated_value = gv.DayConfig(**value).model_dump()
        self.environment["sky"].update(validated_value)

    @property
    def sun_times(self) -> gv.SunTimes | None:
        return self.general.sun_times


# ---------------------------------------------------------------------------
#   Functions to interact with the module
# ---------------------------------------------------------------------------
def get_IDs(ecosystem: str) -> gv.IDs:
    """Return the tuple (ecosystem_uid, ecosystem_name)

    :param ecosystem: str, either an ecosystem uid or ecosystem name
    """
    return EngineConfig().get_IDs(ecosystem)


def detach_config(ecosystem: str) -> None:
    config = EcosystemConfig(ecosystem=ecosystem)
    del _MetaEcosystemConfig.instances[config.uid]
