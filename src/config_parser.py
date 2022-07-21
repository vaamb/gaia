from collections import namedtuple
from contextlib import contextmanager
from datetime import datetime, time
import json
import logging
import pathlib
import random
import string
from threading import Condition, Event, Lock, Thread
from typing import Union
import weakref

from .exceptions import HardwareNotFound, UndefinedParameter
from .hardware import HARDWARE_AVAILABLE
from .hardware.ABC import Hardware
from .subroutines import SUBROUTINES
from .utils import (
    base_dir, file_hash, get_coordinates, human_time_parser, is_connected,
    SingletonMeta, utc_time_to_local_time, yaml
)
from config import Config

config_event = Condition()
_lock = Lock()


logger = logging.getLogger(f"{Config.APP_NAME.lower()}.config")


IDsTuple = namedtuple("IDsTuple", ("uid", "name"))


# ---------------------------------------------------------------------------
#   default ecosystem configuration
# ---------------------------------------------------------------------------
DEFAULT_ECOSYSTEM_CFG = {
    "name": "",
    "status": False,
    "management": {
        "sensors": False,
        "light": False,
        "climate": False,
        "watering": False,
        "health": False,
        "alarms": False,
        "webcam": False,
    },
    "environment": {},
    "IO": {},
}


# ---------------------------------------------------------------------------
#   GeneralConfig class
# ---------------------------------------------------------------------------
class GeneralConfig(metaclass=SingletonMeta):
    """Class to interact with the configuration files

    To interact with a specific ecosystem configuration, the SpecificConfig
    class should be used.
    """
    def __init__(self, base_dir=base_dir) -> None:
        logger.debug("Initializing GeneralConfig")
        self._base_dir = pathlib.Path(base_dir)
        self._ecosystems_config: dict = {}
        self._private_config: dict = {}
        self._hash_dict = {}
        self._stop_event = Event()
        self._watchdog_pause = Event()
        self._watchdog_pause.set()
        self._thread = None
        self._started = False
        for cfg in ("ecosystems", "private"):
            self._load_or_create_config(cfg)

    def __repr__(self) -> str:
        return f"GeneralConfig(watchdog={self._started})"

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
        except IOError:
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
            old_hash = dict(self._hash_dict)
            self._update_cfg_hash()
            reload_cfg = [
                cfg for cfg in ("ecosystems", "private")
                if old_hash[cfg] != self._hash_dict[cfg]
            ]
            if reload_cfg:
                logger.info(f"Change in config detected, updating it.")
                self.reload(reload_cfg)
            self._stop_event.wait(Config.CONFIG_WATCHER_PERIOD)

    def start_watchdog(self) -> None:
        if not self._started:
            logger.info("Starting the configuration files watchdog")
            self._update_cfg_hash()
            self._thread = Thread(target=self._watchdog_loop)
            self._thread.name = "config_watchdog"
            self._thread.start()
            self._started = True
            logger.debug("Configuration files watchdog successfully started")
        else:  # pragma: no cover
            logger.debug("Configuration files watchdog is already running")

    def stop_watchdog(self) -> None:
        if self._started:
            logger.info("Stopping the configuration files watchdog")
            self._stop_event.set()
            self._thread.join()
            self._thread = None
            self._started = False
            logger.debug("Configuration files watchdog successfully stopped")

    @contextmanager
    def pausing_watchdog(self):
        with _lock:  # maybe use a semaphore?
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
        ecosystem_cfg = {uid: DEFAULT_ECOSYSTEM_CFG}
        ecosystem_cfg[uid]["name"] = ecosystem_name
        self._ecosystems_config.update(ecosystem_cfg)

    @property
    def ecosystems_config(self) -> dict:
        return self._ecosystems_config

    @ecosystems_config.setter
    def ecosystems_config(self, value: dict):
        if Config.TESTING:
            self._ecosystems_config = value
        else:
            raise AttributeError("can't set attribute 'ecosystems_config'")

    @property
    def private_config(self) -> dict:
        return self._private_config

    @private_config.setter
    def private_config(self, value: dict):
        if Config.TESTING:
            self._private_config = value
        else:
            raise AttributeError("can't set attribute 'private_config'")

    @property
    def base_dir(self) -> pathlib.Path:
        return self._base_dir

    @property
    def ecosystems_uid(self) -> list:
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

    def get_ecosystems_expected_running(self) -> set:
        return set([
            ecosystem_uid for ecosystem_uid in self._ecosystems_config
            if self._ecosystems_config[ecosystem_uid]["status"]
        ])

    def get_IDs(self, ecosystem: str) -> IDsTuple:
        if ecosystem in self.ecosystems_uid:
            ecosystem_uid = ecosystem
            ecosystem_name = self.id_to_name_dict[ecosystem]
            return IDsTuple(ecosystem_uid, ecosystem_name)
        elif ecosystem in self.ecosystems_name:
            ecosystem_uid = self.name_to_id_dict[ecosystem]
            ecosystem_name = ecosystem
            return IDsTuple(ecosystem_uid, ecosystem_name)
        raise ValueError(
            "'ecosystem' parameter should either be an ecosystem uid or an "
            "ecosystem name present in the 'ecosystems.cfg' file. If you want "
            "to create a new ecosystem configuration use the function "
            "'createConfig()'."
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
            try:
                coordinates = get_coordinates(self.home_city)
                self._private_config["places"]["home"]["coordinates"] = \
                    coordinates
                self.save("private")  # save to not reuse geopy api
                return coordinates
            except LookupError:
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


# ---------------------------------------------------------------------------
#   SpecificConfig class
# ---------------------------------------------------------------------------
class SpecificConfig:
    def __init__(self, general_config: GeneralConfig, ecosystem: str) -> None:
        self._general_config = weakref.proxy(general_config)
        ids = self._general_config.get_IDs(ecosystem)
        logger.debug(f"Initializing SpecificConfig for ecosystem {ids.name}")
        self.uid = ids.uid
        # TODO: add missing managements in dict and set to false

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.uid}, name={self.name}, " \
               f"general_config={self._general_config})"

    @property
    def ecosystem_config(self) -> dict:
        return self._general_config.ecosystems_config[self.uid]

    @ecosystem_config.setter
    def ecosystem_config(self, value: dict):
        if Config.TESTING:
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
    def get_management(self, parameter: str) -> bool:
        try:
            return self.ecosystem_config["management"].get(parameter, False)
        except (KeyError, AttributeError):  # pragma: no cover
            return False

    def set_management(self, management: str, value: bool) -> None:
        self.ecosystem_config["management"][management] = value

    def get_managed_subroutines(self) -> list:
        return [subroutine for subroutine in SUBROUTINES
                if self.get_management(subroutine)]

    """Environment related parameters"""
    @property
    def light_method(self) -> str:
        try:
            method = self.ecosystem_config["environment"]["light"]
            if method in ("elongate", "mimic"):
                if not is_connected():
                    logger.warning(
                        "Not connected to the internet, light method "
                        "automatically turned to 'fixed'"
                    )
                    return "fixed"
            return method
        except KeyError:  # pragma: no cover
            raise UndefinedParameter(
                "Define ['environment']['light'] or remove light management"
            )

    @light_method.setter
    def light_method(self, method) -> None:
        if method not in ("elongate", "fixed", "mimic"):
            raise ValueError("method should be 'elongate', 'fixed' or 'mimic'")
        self.ecosystem_config["environment"]["light"] = method

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

    def get_climate_parameters(self, parameter: str) -> dict:
        environment = self.ecosystem_config["environment"]
        try:
            data = {
                tod: environment[tod]["climate"][parameter]
                for tod in ("day", "night")
            }
        except KeyError:
            raise UndefinedParameter
        else:
            try:
                data["hysteresis"] = environment["hysteresis"].get(parameter, 0)
            except KeyError:
                data["hysteresis"] = 0
            finally:
                return data

    def set_climate_parameters(self, parameter: str, value: dict) -> None:
        if parameter not in ("temperature", "humidity"):
            raise ValueError("parameter should be set to either 'temperature' "
                             "or 'humidity'")
        if not isinstance(value, dict):
            raise ValueError("value should be a dict with keys equal to 'day' \
                             and 'night' and values equal to the required \
                             parameter")
        environment = self.ecosystem_config["environment"]
        for tod in ("day", "night"):
            try:
                environment[tod]["climate"][parameter] = value[tod]
            except KeyError:
                if not environment.get(tod):
                    environment[tod] = {"climate": {}}
                if not environment[tod].get("climate"):
                    environment[tod]["climate"] = {}
                environment[tod]["climate"][parameter] = value[tod]
        if "hysteresis" in value:
            if not environment.get("hysteresis"):
                environment["hysteresis"] = {}
            environment["hysteresis"][parameter] = value["hysteresis"]

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

    def get_IO_group(self,
                     IO_type: str,
                     level: tuple = ("environment", "plants")
                     ) -> list:
        return [uid for uid in self.IO_dict
                if self.IO_dict[uid]["type"].lower() == IO_type
                and self.IO_dict[uid]["level"].lower() in level]

    def get_IO(self, uid: str) -> dict:
        try:
            return self.IO_dict[uid]
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
        if not Config.TESTING:
            self._general_config.save("ecosystems")

    def create_new_hardware(
        self,
        name: str = "",
        address: str = "",
        model: str = "",
        type: str = "",
        level: str = "",
        measure: list = [],
        plant: str = "",
    ) -> Hardware:
        """
        Create a new hardware
        :param name: str, the name of the hardware to create
        :param address: str: the address of the hardware to create
        :param model: str: the name of the model of the hardware to create
        :param _type: str: the type of hardware to create ('sensor', 'light' ...)
        :param level: str: either 'environment' or 'plants'
        :param measure: list: the list of the measures taken
        :param plant: str: the name of the plant linked to the hardware
        """
        if address in self._used_addresses():
            raise ValueError(f"Address {address} already used")
        if model not in HARDWARE_AVAILABLE:
            raise ValueError(
                "This hardware model is not supported. Use "
                "'SpecificConfig.supported_hardware()' to see supported hardware"
            )
        h = HARDWARE_AVAILABLE[model]
        uid = self._create_new_IO_uid()
        new_hardware = h(
            subroutine="hardware_creation",  # Just need the dict repr
            uid=uid,
            name=name,
            address=address,
            model=model,
            type=type,
            level=level,
            measure=measure,
            plant=plant,
        ).dict_repr
        new_hardware.pop("uid")
        self.IO_dict.update({uid: new_hardware})
        self.save()
        return new_hardware

    def delete_hardware(self, uid) -> None:
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
        return [h for h in HARDWARE_AVAILABLE]

    """Parameters related to time"""
    @property
    def time_parameters(self) -> dict:
        try:
            parameters = {}
            day = self.ecosystem_config["environment"]["day"]["start"]
            parameters["day"] = human_time_parser(day)
            night = self.ecosystem_config["environment"]["night"]["start"]
            parameters["night"] = human_time_parser(night)
            return parameters
        except (KeyError, AttributeError):
            raise UndefinedParameter

    @time_parameters.setter
    def time_parameters(self, value: dict) -> None:
        """Set time parameters

        :param value: A dict in the form {'day': '8h00', 'night': '22h00'}
        """
        if not (value.get("day") and value.get("night")):
            raise ValueError(
                "value should be a dict with keys equal to 'day' and 'night' "
                "and values equal to string representing a human readable time, "
                "such as '20h00'"
            )
        for tod in ("day", "night"):
            try:
                self.ecosystem_config["environment"][tod]["start"] = \
                    value[tod]
            except KeyError:
                if not self.ecosystem_config["environment"].get(tod):
                    self.ecosystem_config["environment"][tod] = {}
                self.ecosystem_config["environment"][tod]["start"] =\
                    value["day"]

    @property
    def sun_times(self) -> dict:
        try:
            with open(self._general_config.base_dir/"cache/sunrise.json", "r") as file:
                payload = json.load(file)
                sunrise = payload["data"]["home"]
        except (IOError, json.decoder.JSONDecodeError, KeyError):
            raise UndefinedParameter

        def import_daytime_event(daytime_event: str) -> time:
            try:
                my_time = datetime.strptime(
                    sunrise[daytime_event], "%I:%M:%S %p").time()
                local_time = utc_time_to_local_time(my_time)
                return local_time
            except Exception:
                raise UndefinedParameter
        return {
            "twilight_begin": import_daytime_event("civil_twilight_begin"),
            "sunrise": import_daytime_event("sunrise"),
            "sunset": import_daytime_event("sunset"),
            "twilight_end": import_daytime_event("civil_twilight_end"),
        }


# ---------------------------------------------------------------------------
#   Functions to interact with the module
# ---------------------------------------------------------------------------
_configs = {}


def get_general_config() -> GeneralConfig:
    return GeneralConfig()


def get_config(ecosystem: str) -> SpecificConfig:
    """ Return the specificConfig object for the given ecosystem.

    If no ecosystem is provided, return the globalConfig object instead

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


def get_IDs(ecosystem: str) -> IDsTuple:
    """Return the tuple (ecosystem_uid, ecosystem_name)

    :param ecosystem: str, either an ecosystem uid or ecosystem name
    """
    return get_general_config().get_IDs(ecosystem)


def detach_config(ecosystem) -> None:
    uid = get_general_config().get_IDs(ecosystem).uid
    del _configs[uid]
