from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, time
from json.decoder import JSONDecodeError
import logging
import pathlib
import random
import requests
import string
from threading import Condition, Event, Lock, Thread
from typing import cast, TypedDict, Union
import weakref

from gaia_validators import (
    ClimateParameterNames, ClimateConfig, DayConfig, EnvironmentConfig,
    EnvironmentConfigDict, safe_enum_from_name, IDs, HardwareConfig,
    HardwareConfigDict, HardwareLevelNames, HardwareTypeNames, LightMethod,
    LightMethodNames, ManagementConfig, ManagementNames, SunTimes
)

from gaia.config import (
    get_base_dir, get_cache_dir, get_config as get_gaia_config
)
from gaia.exceptions import HardwareNotFound, UndefinedParameter
from gaia.hardware import hardware_models
from gaia.subroutines import SUBROUTINES
from gaia.utils import (
    file_hash, is_connected, json, SingletonMeta, utc_time_to_local_time, yaml
)


_store = {}


def get_config_event():
    try:
        return _store["config_event"]
    except KeyError:
        _store["config_event"] = Condition()
        return _store["config_event"]


logger = logging.getLogger("gaia.config.environments")


# ---------------------------------------------------------------------------
#   default ecosystem configuration
# ---------------------------------------------------------------------------
class EcosystemDict(TypedDict):
    name: str
    status: bool
    management: dict[ManagementNames, bool]
    environment: EnvironmentConfigDict
    IO: dict[str, HardwareConfigDict]


DEFAULT_ECOSYSTEM_CFG = EcosystemDict(
    name="",
    status=False,
    management=cast(dict[ManagementNames, bool], asdict(ManagementConfig())),
    environment=cast(EnvironmentConfigDict, asdict(EnvironmentConfig())),
    IO={},
)


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
        self._last_sun_times_update: datetime = datetime(1970, 1, 1)
        self._hash_dict: dict[str, str] = {}
        self._lock = Lock()
        self._stop_event = Event()
        self._watchdog_pause = Event()
        self._watchdog_pause.set()
        self._thread: Thread | None = None
        for cfg in ("ecosystems", "private"):
            self._load_or_create_config(cfg)

    def __repr__(self) -> str:
        return f"GeneralConfig(watchdog={self.started})"

    @property
    def started(self) -> bool:
        return self._thread is not None

    def _load_config(self, cfg: str) -> None:
        config_path = self._base_dir / f"{cfg}.cfg"
        if cfg == "ecosystems":
            with open(config_path, "r") as file:
                self._ecosystems_config = yaml.load(file)
        elif cfg == "private":
            with open(config_path, "r") as file:
                self._private_config = yaml.load(file)
        else:  # pragma: no cover
            raise ValueError("cfg should be 'ecosystems' or 'private'")

    def _dump_config(self, cfg: str):
        config_path = self._base_dir / f"{cfg}.cfg"
        if cfg == "ecosystems":
            with open(config_path, "w") as file:
                yaml.dump(self._ecosystems_config, file)
        elif cfg == "private":
            with open(config_path, "w") as file:
                yaml.dump(self._private_config, file)
        else:  # pragma: no cover
            raise ValueError("cfg should be 'ecosystems' or 'private'")

    def _load_or_create_config(self, cfg: str) -> None:
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
                self._private_config = {"places": {}}
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

    def start_watchdog(self) -> None:
        if not self.started:
            logger.info("Starting the configuration files watchdog")
            self._update_cfg_hash()
            self._thread = Thread(target=self._watchdog_loop)
            self._thread.name = "config_watchdog"
            self._thread.start()
            logger.debug("Configuration files watchdog successfully started")
        else:  # pragma: no cover
            logger.debug("Configuration files watchdog is already running")

    def stop_watchdog(self) -> None:
        if self.started:
            logger.info("Stopping the configuration files watchdog")
            self._stop_event.set()
            self._thread.join()
            self._thread = None
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

    def reload(self, config: Union[list, str, tuple] = ("ecosystems", "private")) -> None:
        with self.pausing_watchdog():
            if isinstance(config, str):
                config = (config, )
            logger.debug(f"Updating configuration file(s) {tuple(config)}")
            for cfg in config:
                self._load_config(cfg=cfg)
            config_event = get_config_event()
            with config_event:
                config_event.notify_all()

    def save(self, config: Union[list, str, tuple]) -> None:
        with self.pausing_watchdog():
            if isinstance(config, str):
                config = (config, )
            logger.debug(f"Updating configuration file(s) {tuple(config)}")
            for cfg in config:
                self._dump_config(cfg)

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
        ecosystem_cfg: dict[str, EcosystemDict] = {uid: DEFAULT_ECOSYSTEM_CFG}
        ecosystem_cfg[uid]["name"] = ecosystem_name
        self._ecosystems_config.update(ecosystem_cfg)

    @property
    def ecosystems_config(self) -> dict[str, EcosystemDict]:
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
    def last_sun_times_update(self) -> datetime:
        return self._last_sun_times_update

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
        raise ValueError(
            "'ecosystem_id' parameter should either be an ecosystem uid or an "
            "ecosystem name present in the 'ecosystems.cfg' file. If you want "
            "to create a new ecosystem configuration use the function "
            "`create_ecosystem()`."
        )

    """Private config parameters"""
    @property
    def home_city(self) -> str:
        try:
            return self._private_config["places"]["home"]["city"]
        except KeyError:
            raise UndefinedParameter

    @home_city.setter
    def home_city(self, city_name: str) -> None:
        home = self._private_config["places"].get("home", {})
        if not home:
            self._private_config["places"]["home"] = {}
        self._private_config["places"]["home"]["city"] = city_name

    @property
    def home_coordinates(self) -> dict:
        try:
            return self._private_config["places"]["home"]["coordinates"]
        except KeyError:
            raise UndefinedParameter

    @home_coordinates.setter
    def home_coordinates(self, value: tuple) -> None:
        """Set home coordinates

        :param value: A tuple with (latitude, longitude)
        """
        home = self._private_config["places"].get("home", {})
        if not home:
            self._private_config["places"]["home"] = {}
        coordinates = {"latitude": value[0], "longitude": value[1]}
        self._private_config["places"]["home"]["coordinates"] = coordinates

    @property
    def sun_times(self) -> SunTimes:
        try:
            with open(get_cache_dir()/"sunrise.json", "r") as file:
                payload = json.loads(file.read())
                sunrise = payload["data"]["home"]
        except (OSError, JSONDecodeError, KeyError):
            raise UndefinedParameter

        def import_daytime_event(daytime_event: str) -> time:
            try:
                my_time = datetime.strptime(
                    sunrise[daytime_event], "%I:%M:%S %p").astimezone().time()
                local_time = utc_time_to_local_time(my_time)
                return local_time
            except Exception:
                raise UndefinedParameter
        return SunTimes(
            twilight_begin=import_daytime_event("civil_twilight_begin"),
            sunrise=import_daytime_event("sunrise"),
            sunset=import_daytime_event("sunset"),
            twilight_end=import_daytime_event("civil_twilight_end"),
        )

    def download_sun_times(self) -> None:
        sun_times_file = get_cache_dir()/"sunrise.json"
        # Determine if the file needs to be updated
        need_update = False
        try:
            with sun_times_file.open("r") as file:
                sun_times_data = json.loads(file.read())
                last_update: str = sun_times_data["last_update"]
                self._last_sun_times_update = \
                    datetime.fromisoformat(last_update).astimezone()
        except (FileNotFoundError, JSONDecodeError):
            need_update = True
        else:
            if self._last_sun_times_update.date() < date.today():
                need_update = True
            else:
                logger.debug("Sun times already up to date")
        if need_update:
            logger.info("Refreshing sun times")
            try:
                home_coordinates = self.home_coordinates
            except UndefinedParameter:
                logger.error(
                    "You need to define your home city coordinates in "
                    "'private.cfg' in order to update sun times."
                )
            else:
                latitude = home_coordinates["latitude"]
                longitude = home_coordinates["longitude"]
                try:
                    logger.debug(
                        "Trying to update sunrise and sunset times on "
                        "sunrise-sunset.org"
                    )
                    response = requests.get(
                        url=f"https://api.sunrise-sunset.org/json",
                        params={"lat": latitude, "lng": longitude},
                        timeout=3.0,
                        verify=False,  # TODO: change when renewed
                    )
                    data = response.json()
                    results = data["results"]
                except requests.exceptions.ConnectionError:
                    logger.debug(
                        "Failed to update sunrise and sunset times"
                    )
                    raise ConnectionError
                else:
                    self._last_sun_times_update = datetime.now().astimezone()
                    payload = {
                        "last_update": self._last_sun_times_update,
                        "data": {"home": results},
                    }
                    with open(sun_times_file, "w") as file:
                        file.write(json.dumps(payload))
                    logger.info(
                        "Sunrise and sunset times successfully updated"
                    )


# ---------------------------------------------------------------------------
#   SpecificConfig class
# ---------------------------------------------------------------------------
class SpecificConfig:
    def __init__(self, general_config: GeneralConfig, ecosystem: str) -> None:
        self._general_config = weakref.proxy(general_config)
        ids = self._general_config.get_IDs(ecosystem)
        self.uid = ids.uid
        self.logger = logging.getLogger(f"gaia.engine.{ids.name}.config")
        self.logger.debug(f"Initializing SpecificConfig")
        self._first_connection_error = True

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.uid}, name={self.name}, " \
               f"general_config={self._general_config})"

    @property
    def general(self) -> GeneralConfig:
        return self._general_config

    @property
    def ecosystem_config(self) -> EcosystemDict:
        return self._general_config.ecosystems_config[self.uid]

    @ecosystem_config.setter
    def ecosystem_config(self, value: EcosystemDict):
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

    @property
    def status(self) -> bool:
        return self.ecosystem_config["status"]

    @status.setter
    def status(self, value: bool) -> None:
        self.ecosystem_config["status"] = value

    """Parameters related to sub-routines control"""
    def get_management(self, management: ManagementNames) -> bool:
        try:
            return self.ecosystem_config["management"].get(management, False)
        except (KeyError, AttributeError):  # pragma: no cover
            return False

    def set_management(self, management: ManagementNames, value: bool) -> None:
        self.ecosystem_config["management"][management] = value

    def get_managed_subroutines(self) -> list:
        return [subroutine for subroutine in SUBROUTINES
                if self.get_management(subroutine)]

    """EnvironmentConfig related parameters"""
    @property
    def light_method(self) -> LightMethod:
        try:
            method = self.ecosystem_config["environment"]["sky"]["lighting"]
            if method in ("elongate", "mimic"):
                if not is_connected():
                    if self._first_connection_error:
                        self.logger.warning(
                            "Not connected to the internet, light method "
                            "automatically turned to 'fixed'"
                        )
                        self._first_connection_error = False
                    return LightMethod.fixed
            return cast(LightMethod, safe_enum_from_name(LightMethod, method))
        except KeyError:  # pragma: no cover
            raise UndefinedParameter(
                "Define ['environment']['light'] or remove light management"
            )

    @light_method.setter
    def light_method(self, method: LightMethodNames) -> None:
        if not self.ecosystem_config["environment"].get("sky"):
            self.ecosystem_config["environment"]["sky"] = {}
        self.ecosystem_config["environment"]["sky"]["lighting"] = method.value

    @property
    def chaos(self) -> dict:
        try:
            return self.ecosystem_config["environment"]["chaos"]
        except KeyError:
            raise UndefinedParameter

    @chaos.setter
    def chaos(self, values: dict) -> None:
        """Set chaos parameter

        :param values: A dict with the entries 'frequency': int,
                       'duration': int and 'intensity': float.
        """
        environment = self.ecosystem_config["environment"]
        if not environment.get("chaos"):
            environment["chaos"] = {}
        frequency = environment["chaos"].get("frequency", 0)
        duration = environment["chaos"].get("duration", 0)
        intensity = environment["chaos"].get("intensity", 1.0)
        self.ecosystem_config["environment"]["chaos"]["frequency"] = \
            values.get("frequency", frequency)
        self.ecosystem_config["environment"]["chaos"]["duration"] = \
            values.get("duration", duration)
        self.ecosystem_config["environment"]["chaos"]["intensity"] = \
            values.get("intensity", intensity)

    # TODO: use Literal for parameter
    def get_climate_parameters(self, parameter: ClimateParameterNames) -> ClimateConfig:
        environment = self.ecosystem_config["environment"]
        try:
            data = environment["climate"][parameter]
            return ClimateConfig(parameter=parameter, **data)
        except KeyError:
            raise UndefinedParameter

    # TODO: use Literal for parameter and value
    def set_climate_parameters(self, parameter: ClimateParameterNames, value: dict) -> None:
        environment = self.ecosystem_config["environment"]
        if not environment.get("climate"):
            environment["climate"] = {}
        environment["climate"][parameter] = value

    """Parameters related to IO"""    
    @property
    def IO_dict(self) -> dict:
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

    def get_hardware_config(self, uid: str) -> HardwareConfig:
        try:
            hardware_config = self.IO_dict[uid]
            hardware_config["uid"] = uid
            return HardwareConfig(**hardware_config)
        except KeyError:
            raise HardwareNotFound

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

    def save(self):
        if not get_gaia_config().TESTING:
            self._general_config.save("ecosystems")

    def create_new_hardware(
        self,
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

    @staticmethod
    def supported_hardware() -> list:
        return [h for h in hardware_models]

    """Parameters related to time"""
    @property
    def time_parameters(self) -> DayConfig:
        try:
            return DayConfig(
                day=self.ecosystem_config["environment"]["sky"]["day"],
                night=self.ecosystem_config["environment"]["sky"]["night"],
            )
        except (KeyError, AttributeError):
            raise UndefinedParameter

    @time_parameters.setter
    def time_parameters(self, value: dict[str, str]) -> None:
        """Set time parameters

        :param value: A dict in the form {'day': '8h00', 'night': '22h00'}
        """
        if not (value.get("day") and value.get("night")):
            raise ValueError(
                "value should be a dict with keys equal to 'day' and 'night' "
                "and values equal to string representing a human readable time, "
                "such as '20h00'"
            )
        if not self.ecosystem_config["environment"].get("sky"):
            self.ecosystem_config["environment"]["sky"] = {}
        for tod in ("day", "night"):
            self.ecosystem_config["environment"]["sky"][tod] = value[tod]

    @property
    def sun_times(self) -> SunTimes:
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
