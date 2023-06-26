from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time
from json.decoder import JSONDecodeError
import logging
import pathlib
import random
import requests
import string
from threading import Condition, Event, Lock, Thread
from typing import Literal, TypedDict
import weakref

from pydantic import BaseModel, Field, ValidationError, validator

from gaia_validators import *

from gaia.config import (
    get_base_dir, get_cache_dir, get_config as get_gaia_config)
from gaia.exceptions import (
    EcosystemNotFound, HardwareNotFound, UndefinedParameter)
from gaia.hardware import hardware_models
from gaia.subroutines import SUBROUTINES
from gaia.utils import (
    file_hash, json, SingletonMeta, utc_time_to_local_time, yaml)


_store = {}


ConfigType = Literal["ecosystems", "private"]


def get_config_event():
    try:
        return _store["config_event"]
    except KeyError:
        _store["config_event"] = Condition()
        return _store["config_event"]


logger = logging.getLogger("gaia.config.environments")


# ---------------------------------------------------------------------------
#   Common config models
# ---------------------------------------------------------------------------
class SunTimesDict(TypedDict):
    civil_twilight_begin: str
    sunrise: str
    sunset: str
    civil_twilight_end: str


class _SunTimesFileHomeDict(TypedDict):
    home: SunTimesDict


class SunTimesFileDict(TypedDict):
    last_update: str
    data: _SunTimesFileHomeDict


class Coordinates(BaseModel):
    latitude: float
    longitude: float


class CoordinatesDict(TypedDict):
    latitude: float
    longitude: float


class Place(BaseModel):
    name: str
    coordinates: Coordinates


class PlaceDict(TypedDict):
    name: str
    coordinates: CoordinatesDict


# ---------------------------------------------------------------------------
#   Ecosystem config models
# ---------------------------------------------------------------------------
# Custom models for Hardware, Climate and Environment configs as some of their
#  parameters are used as keys in ecosystems.cfg
class _HardwareConfig(BaseModel):
    name: str
    address: str
    type: str
    level: str
    model: str
    measures: list[str] = Field(default_factory=list, alias="measure")
    plants: list[str] = Field(default_factory=list, alias="plant")
    multiplexer_model: str | None = Field(default=None, alias="multiplexer")

    class Config:
        allow_population_by_field_name = True

    @validator("measures", "plants", pre=True)
    def parse_to_list(cls, value: str | list | None):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value

    @validator("address", "type", "level", "measures", pre=True)
    def lower_str(cls, value: str | list | None):
        if isinstance(value, str):
            return value.lower()
        if isinstance(value, list):
            accumulator = []
            for v in value:
                if isinstance(v, str):
                    v = v.lower()
                accumulator.append(v)
            return accumulator
        return value


class _HardwareConfigDict(TypedDict):
    name: str
    address: str
    type: str
    level: str
    model: str
    measures: list[str]
    plants: list[str]
    multiplexer_model: str | None


class _ClimateConfig(BaseModel):
    day: float
    night: float
    hysteresis: float = 0.0


class _ClimateConfigDict(TypedDict):
    day: float
    night: float
    hysteresis: float


class _EnvironmentConfig(BaseModel):
    chaos: ChaosConfig = Field(default_factory=ChaosConfig)
    sky: SkyConfig = Field(default_factory=SkyConfig)
    climate: dict[ClimateParameterNames, _ClimateConfig] = Field(default_factory=dict)

    @validator("climate", pre=True)
    def dict_to_climate(cls, value: dict):
        return {k: _ClimateConfig(**v) for k, v in value.items()}


class _EnvironmentConfigDict(TypedDict):
    chaos: ChaosConfigDict
    sky: SkyConfigDict
    climate: dict[str, _ClimateConfigDict]


class EcosystemConfig(BaseModel):
    name: str
    status: bool = False
    management: ManagementConfig = Field(default_factory=ManagementConfig)
    environment: _EnvironmentConfig = Field(default_factory=_EnvironmentConfig)
    IO: dict[str, _HardwareConfig] = Field(default_factory=dict)


class EcosystemConfigDict(TypedDict):
    name: str
    status: bool
    management: dict[ManagementNames, bool]
    environment: _EnvironmentConfigDict
    IO: dict[str, _HardwareConfigDict]


class _EcosystemsConfig(BaseModel):
    config: dict[str, EcosystemConfig]


# ---------------------------------------------------------------------------
#   GeneralConfig class
# ---------------------------------------------------------------------------
class GeneralConfig(metaclass=SingletonMeta):
    """Class to interact with the configuration files

    To interact with a specific ecosystem configuration, the SpecificConfig
    class should be used.
    """
    def __init__(self, base_dir=get_base_dir()) -> None:
        logger.debug("Initializing GeneralConfig")
        self._base_dir = pathlib.Path(base_dir)
        self._ecosystems_config: dict = {}
        self._private_config: dict = {}
        self._sun_times: SunTimes | None = None
        self._hash_dict: dict[str, str] = {}
        self._lock = Lock()
        self._stop_event = Event()
        self._watchdog_pause = Event()
        self._watchdog_pause.set()
        self._thread: Thread | None = None
        for cfg in ("ecosystems", "private"):
            cfg: ConfigType
            self._load_or_create_config(cfg)

    def __repr__(self) -> str:
        return f"GeneralConfig(watchdog={self.started})"

    @property
    def started(self) -> bool:
        return self._thread is not None

    def _load_config(self, cfg: ConfigType) -> None:
        config_path = self._base_dir / f"{cfg}.cfg"
        if cfg == "ecosystems":
            with open(config_path, "r") as file:
                raw = {"config": yaml.load(file)}
                try:
                    cleaned = _EcosystemsConfig(**raw).dict()
                except ValidationError as e:
                    # TODO: log formatted error message
                    raise e
                else:
                    self._ecosystems_config = cleaned["config"]
        elif cfg == "private":
            with open(config_path, "r") as file:
                self._private_config = yaml.load(file)
        else:  # pragma: no cover
            raise ValueError("cfg should be 'ecosystems' or 'private'")

    def _dump_config(self, cfg: ConfigType):
        config_path = self._base_dir / f"{cfg}.cfg"
        if cfg == "ecosystems":
            with open(config_path, "w") as file:
                yaml.dump(self._ecosystems_config, file)
        elif cfg == "private":
            with open(config_path, "w") as file:
                yaml.dump(self._private_config, file)
        else:  # pragma: no cover
            raise ValueError("cfg should be 'ecosystems' or 'private'")

    def _load_or_create_config(self, cfg: ConfigType) -> None:
        try:
            self._load_config(cfg)
        except OSError:
            if cfg == "ecosystems":
                logger.warning(
                    "There is currently no custom ecosystem configuration file. "
                    "Creating a default configuration instead"
                )
                self._ecosystems_config = {}
                self.create_ecosystem("Default Ecosystem")
                self.save("ecosystems")
            elif cfg == "private":
                logger.warning(
                    "There is currently no custom private configuration file. "
                    "Using the default settings instead"
                )
                self._private_config = {}
                self.save("private")

    def _update_cfg_hash(self) -> None:
        for cfg in ("ecosystems", "private"):
            path = self._base_dir/f"{cfg}.cfg"
            self._hash_dict[cfg] = file_hash(path)

    def _watchdog_loop(self) -> None:
        while not self._stop_event.is_set():
            self._watchdog_pause.wait()
            old_hash = {**self._hash_dict}
            self._update_cfg_hash()
            reload_cfg = [
                cfg for cfg in ("ecosystems", "private")
                if old_hash[cfg] != self._hash_dict[cfg]
            ]
            if reload_cfg:
                logger.info(f"Change in config file(s) detected, updating GeneralConfig.")
                self.reload(reload_cfg)
            self._stop_event.wait(get_gaia_config().CONFIG_WATCHER_PERIOD)

    @property
    def thread(self) -> Thread:
        if self._thread is None:
            raise RuntimeError("Thread has not been set up")
        else:
            return self._thread

    @thread.setter
    def thread(self, thread: Thread | None):
        if not isinstance(thread, Thread):
            raise ValueError
        self._thread = thread

    def start_watchdog(self) -> None:
        if not self.started:
            logger.info("Starting the configuration files watchdog")
            self._update_cfg_hash()
            self.thread = Thread(target=self._watchdog_loop)
            self.thread.name = "config_watchdog"
            self.thread.start()
            logger.debug("Configuration files watchdog successfully started")
        else:  # pragma: no cover
            logger.debug("Configuration files watchdog is already running")

    def stop_watchdog(self) -> None:
        if self.started:
            logger.info("Stopping the configuration files watchdog")
            self._stop_event.set()
            self.thread.join()
            self.thread = None
            logger.debug("Configuration files watchdog successfully stopped")

    @contextmanager
    def pausing_watchdog(self):
        with self._lock:  # maybe use a semaphore?
            try:
                self._watchdog_pause.clear()
                yield
            finally:
                self._update_cfg_hash()
                self._watchdog_pause.set()

    def reload(self, config: list | str | tuple = ("ecosystems", "private")) -> None:
        with self.pausing_watchdog():
            if isinstance(config, str):
                config = (config, )
            logger.debug(f"Updating configuration file(s) {tuple(config)}")
            for cfg in config:
                self._load_config(cfg=cfg)
            config_event = get_config_event()
            with config_event:
                config_event.notify_all()

    def save(self, config: ConfigType) -> None:
        with self.pausing_watchdog():
            logger.debug(f"Updating configuration file(s) {config}")
            self._dump_config(config)

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

    def create_ecosystem(self, ecosystem_name: str) -> None:
        uid = self._create_new_ecosystem_uid()
        ecosystem_cfg = EcosystemConfig(name=ecosystem_name).dict()
        self._ecosystems_config.update({uid: ecosystem_cfg})
        self.save("ecosystems")

    def delete_ecosystem(self, ecosystem_id: str) -> None:
        ecosystem_ids = self.get_IDs(ecosystem_id)
        del self._ecosystems_config[ecosystem_ids.uid]
        self.save("ecosystems")

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

    def get_IDs(self, ecosystem_id: str) -> IDs:
        if ecosystem_id in self.ecosystems_uid:
            ecosystem_uid = ecosystem_id
            ecosystem_name = self.id_to_name_dict[ecosystem_id]
            return IDs(ecosystem_uid, ecosystem_name)
        elif ecosystem_id in self.ecosystems_name:
            ecosystem_uid = self.name_to_id_dict[ecosystem_id]
            ecosystem_name = ecosystem_id
            return IDs(ecosystem_uid, ecosystem_name)
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

    def get_place(self, place: str) -> Place:
        try:
            coordinates: CoordinatesDict = self.places[place]
            return Place(name=place, coordinates=coordinates)
        except KeyError:
            raise UndefinedParameter

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
        validated_coordinates: CoordinatesDict = Coordinates(**coordinates).dict()
        self.places[place] = validated_coordinates

    @property
    def home(self) -> Place:
        return self.get_place("home")

    @home.setter
    def home(self, coordinates: tuple[float, float] | CoordinatesDict) -> None:
        self.set_place("home", coordinates=coordinates)

    @property
    def home_name(self) -> str:
        return self.home.name

    @property
    def home_coordinates(self) -> Coordinates:
        return self.home.coordinates

    @property
    def units(self) -> dict[str, str]:
        return self._private_config.get("units", {})

    @property
    def sun_times(self) -> SunTimes | None:
        return self._sun_times

    def refresh_sun_times(self) -> None:
        sun_times_file = get_cache_dir()/"sunrise.json"
        # Determine if the file needs to be updated
        sun_times_data: SunTimesDict | None = None
        try:
            with sun_times_file.open("r") as file:
                payload: SunTimesFileDict = json.loads(file.read())
                last_update: datetime = \
                    datetime.fromisoformat(payload["last_update"]).astimezone()
        except (FileNotFoundError, JSONDecodeError, KeyError):
            pass
        else:
            if last_update.date() >= date.today():
                sun_times_data = payload["data"]["home"]
                logger.debug("Sun times already up to date")
        if sun_times_data is None:
            sun_times_data = self.download_sun_times()

        if sun_times_data is not None:
            def import_daytime_event(daytime_event: str) -> time:
                try:
                    my_time = datetime.strptime(
                        sun_times_data[daytime_event], "%I:%M:%S %p").astimezone().time()
                    local_time = utc_time_to_local_time(my_time)
                    return local_time
                except Exception:
                    raise UndefinedParameter

            self._sun_times = SunTimes(
                twilight_begin=import_daytime_event("civil_twilight_begin"),
                sunrise=import_daytime_event("sunrise"),
                sunset=import_daytime_event("sunset"),
                twilight_end=import_daytime_event("civil_twilight_end"),
            )

        else:
            self._sun_times = None

    def download_sun_times(self) -> SunTimesDict | None:
        sun_times_file = get_cache_dir()/"sunrise.json"
        logger.info("Refreshing sun times")
        try:
            home_coordinates = self.home_coordinates
        except UndefinedParameter:
            logger.error(
                "You need to define your home city coordinates in "
                "'private.cfg' in order to update sun times."
            )
            return None
        else:
            try:
                logger.debug(
                    "Trying to update sunrise and sunset times on "
                    "sunrise-sunset.org")
                response = requests.get(
                    url=f"https://api.sunrise-sunset.org/json",
                    params={
                        "lat": home_coordinates.latitude,
                        "lng": home_coordinates.longitude
                    },
                    timeout=3.0,
                )
                data = response.json()
                results: SunTimesDict = data["results"]
            except requests.exceptions.ConnectionError:
                logger.debug(
                    "Failed to update sunrise and sunset times"
                )
                return None
            else:
                payload: SunTimesFileDict = {
                    "last_update": datetime.now().astimezone().isoformat(),
                    "data": {"home": results},
                }
                with open(sun_times_file, "w") as file:
                    file.write(json.dumps(payload))
                logger.info(
                    "Sunrise and sunset times successfully updated")
                return results


# ---------------------------------------------------------------------------
#   SpecificConfig class
# ---------------------------------------------------------------------------
class SpecificConfig:
    def __init__(self, general_config: GeneralConfig, ecosystem: str) -> None:
        self._general_config: GeneralConfig = weakref.proxy(general_config)
        ids = self._general_config.get_IDs(ecosystem)
        self.uid = ids.uid
        self.logger = logging.getLogger(f"gaia.engine.{ids.name}.config")
        self.logger.debug(f"Initializing SpecificConfig for {ids.name}")
        self._first_connection_error = True

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.uid}, name={self.name}, " \
               f"general_config={self._general_config})"

    def save(self) -> None:
        if not get_gaia_config().TESTING:
            self._general_config.save("ecosystems")

    @property
    def general(self) -> GeneralConfig:
        return self._general_config

    @property
    def ecosystem_config(self) -> EcosystemConfigDict:
        return self._general_config.ecosystems_config[self.uid]

    @ecosystem_config.setter
    def ecosystem_config(self, value: EcosystemConfigDict) -> None:
        if get_gaia_config().TESTING:
            self._general_config.ecosystems_config[self.uid] = value
        else:
            raise AttributeError("can't set attribute 'ecosystem_config'")

    @property
    def name(self) -> str:
        return self.ecosystem_config["name"]

    @name.setter
    def name(self, value: str) -> None:
        self.ecosystem_config["name"] = value
        self.save()

    @property
    def status(self) -> bool:
        return self.ecosystem_config["status"]

    @status.setter
    def status(self, value: bool) -> None:
        self.ecosystem_config["status"] = value
        self.save()

    """Parameters related to sub-routines control"""
    def get_management(self, management: ManagementNames) -> bool:
        try:
            return self.ecosystem_config["management"].get(management, False)
        except (KeyError, AttributeError):  # pragma: no cover
            return False

    def set_management(self, management: ManagementNames, value: bool) -> None:
        if management not in get_enum_names(ManagementFlags):
            raise ValueError(f"{management} is not a valid management parameter")
        self.ecosystem_config["management"][management] = value
        self.save()

    def get_managed_subroutines(self) -> list[ManagementNames]:
        return [subroutine for subroutine in SUBROUTINES
                if self.get_management(subroutine)]

    """EnvironmentConfig related parameters"""
    @property
    def environment(self) -> _EnvironmentConfigDict:
        """
        Returns the environment config for the ecosystem
        """
        try:
            return self.ecosystem_config["environment"]
        except KeyError:
            self.ecosystem_config["environment"] = _EnvironmentConfig().dict()
            return self.ecosystem_config["environment"]

    @property
    def sky(self) -> SkyConfigDict:
        """
        Returns the sky config for the ecosystem
        """
        try:
            return self.environment["sky"]
        except KeyError:
            self.environment["sky"] = SkyConfig().dict()
            return self.environment["sky"]

    @property
    def light_method(self) -> LightMethod:
        if self.sun_times is None:
            return LightMethod.fixed
        return safe_enum_from_name(LightMethod, self.sky["lighting"])

    @light_method.setter
    def light_method(self, method: LightMethod) -> None:
        try:
            validated_method = safe_enum_from_name(LightMethod, method)
        except KeyError:
            raise ValueError("'method' is not a valid 'LightMethod'")
        self.sky["lighting"] = validated_method
        self.save()

    @property
    def chaos(self) -> ChaosConfig:
        try:
            return ChaosConfig(**self.environment["chaos"])
        except KeyError:
            raise UndefinedParameter

    @chaos.setter
    def chaos(self, values: ChaosConfigDict) -> None:
        """Set chaos parameter

        :param values: A dict with the entries 'frequency': int,
                       'duration': int and 'intensity': float.
        """
        validated_values = ChaosConfig(**values).dict()
        self.environment["chaos"] = validated_values
        self.save()

    @property
    def climate(self) -> dict[ClimateParameterNames, _ClimateConfigDict]:
        """
        Returns the sky config for the ecosystem
        """
        try:
            return self.environment["climate"]
        except KeyError:
            self.environment["climate"] = {}
            return self.environment["climate"]

    def get_climate_parameters(self, parameter: ClimateParameterNames) -> ClimateConfig:
        try:
            data = self.climate[parameter]
            return ClimateConfig(parameter=parameter, **data)
        except KeyError:
            raise UndefinedParameter

    def set_climate_parameters(
            self,
            parameter: ClimateParameterNames,
            value: _ClimateConfigDict
    ) -> None:
        validated_value = _ClimateConfig(**value).dict()
        self.climate[parameter] = validated_value
        self.save()

    """Parameters related to IO"""    
    @property
    def IO_dict(self) -> dict[str, _HardwareConfigDict]:
        """
        Returns the IOs (hardware) present in the ecosystem
        """
        try:
            return self.ecosystem_config["IO"]
        except KeyError:
            self.ecosystem_config["IO"] = {}
            return self.ecosystem_config["IO"]

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
            type: HardwareTypeNames,
            level: HardwareLevelNames,
            measures: list | None = None,
            plants: list | None = None,
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
                "'SpecificConfig.supported_hardware()' to see supported hardware"
            )
        uid = self._create_new_IO_uid()
        h = hardware_models[model]
        hardware_config = HardwareConfig(
            uid=uid,
            name=name,
            address=address,
            type=type,
            level=level,
            model=model,
            measures=measures,
            plants=plants
        )
        new_hardware = h.from_hardware_config(hardware_config, None)
        hardware_repr = new_hardware.dict_repr(shorten=True)
        hardware_repr.pop("uid")
        self.IO_dict.update({uid: hardware_repr})
        self.save()

    def update_hardware(self, uid: str, update_value: dict) -> None:
        try:
            non_null_value = {
                key: value for key, value in update_value.items()
                if value is not None
            }
            base = self.IO_dict[uid].copy()
            base.update(non_null_value)
            validated_value = HardwareConfig(uid=uid, **base).dict()
            self.IO_dict[uid] = validated_value
            self.save()
        except KeyError:
            raise HardwareNotFound

    def delete_hardware(self, uid: str) -> None:
        """
        Delete a hardware from the config
        :param uid: str, the uid of the hardware to delete
        """
        try:
            del self.IO_dict[uid]
            self.save()
        except KeyError:
            raise HardwareNotFound

    def get_hardware_config(self, uid: str) -> HardwareConfig:
        try:
            hardware_config = self.IO_dict[uid]
            return HardwareConfig(uid=uid, **hardware_config)
        except KeyError:
            raise HardwareNotFound

    @staticmethod
    def supported_hardware() -> list:
        return [h for h in hardware_models]

    """Parameters related to time"""
    @property
    def time_parameters(self) -> DayConfig:
        return DayConfig(
            day=self.sky["day"],
            night=self.sky["night"],
        )

    @time_parameters.setter
    def time_parameters(self, value: DayConfigDict) -> None:
        """Set time parameters

        :param value: A dict in the form {'day': '8h00', 'night': '22h00'}
        """
        validated_value = DayConfig(**value).dict()
        self.environment["sky"].update(validated_value)

    @property
    def sun_times(self) -> SunTimes | None:
        return self.general.sun_times


# ---------------------------------------------------------------------------
#   Functions to interact with the module
# ---------------------------------------------------------------------------
_configs: dict[str, SpecificConfig] = {}


def get_general_config() -> GeneralConfig:
    return GeneralConfig()


def get_config(ecosystem: str) -> SpecificConfig:
    """ Return the specificConfig object for the given ecosystem.

    :param ecosystem: str, an ecosystem uid or name. If left to none, will
                      return globalConfig object instead.
    """
    general_config = get_general_config()

    ecosystem_uid = general_config.get_IDs(ecosystem).uid
    try:
        return _configs[ecosystem_uid]
    except KeyError:
        _configs[ecosystem_uid] = SpecificConfig(general_config, ecosystem_uid)
        return _configs[ecosystem_uid]


def get_IDs(ecosystem: str) -> IDs:
    """Return the tuple (ecosystem_uid, ecosystem_name)

    :param ecosystem: str, either an ecosystem uid or ecosystem name
    """
    return get_general_config().get_IDs(ecosystem)


def detach_config(ecosystem) -> None:
    uid = get_general_config().get_IDs(ecosystem).uid
    del _configs[uid]
