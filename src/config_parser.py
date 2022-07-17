from collections import namedtuple
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
from .hardware.ABC import Hardware, gpioHardware, i2cHardware
from .subroutines import SUBROUTINES
from .utils import (
    base_dir, file_hash, get_coordinates, human_time_parser, is_connected,
    SingletonMeta, utc_time_to_local_time, yaml
)
from config import Config

config_event = Condition()
lock = Lock()


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
        for cfg in ("ecosystems", "private"):
            self._load_config(cfg)
        self._hash_dict = {}
        self._stop_event = Event()
        self._watchdog_pause = Event()
        self._watchdog_pause.set()
        self._thread = None
        self._started = False
        self.__in_context_manager = False

    def __repr__(self) -> str:
        return f"GeneralConfig(watchdog={self._started})"

    def _load_config(self, cfg: str) -> None:
        if cfg == "ecosystems":
            try:
                custom_cfg = self._base_dir/"ecosystems.cfg"
                with open(custom_cfg, "r") as file:
                    self._ecosystems_config = yaml.load(file)
            except IOError:
                logger.warning(
                    "There is currently no custom ecosystem configuration file. "
                    "Creating a default configuration instead"
                )
                self._ecosystems_config = {}
                self.create_new_ecosystem("Default Ecosystem")
                self.save("ecosystems")

        elif cfg == "private":
            try:
                private_cfg = self._base_dir / "private.cfg"
                with open(private_cfg, "r") as file:
                    self._private_config = yaml.load(file)
            except IOError:
                logger.warning(
                    "There is currently no custom private configuration file. "
                    "Using the default settings instead"
                )
                self._private_config = {}
                self.save("private")
        else:
            raise ValueError("cfg should be 'ecosystems' or 'private'")

    def _update_cfg_hash(self) -> None:
        for cfg in ("ecosystems", "private"):
            path = self._base_dir/f"{cfg}.cfg"
            self._hash_dict[cfg] = file_hash(path)

    def _watchdog_loop(self) -> None:
        while not self._stop_event.is_set():
            self._watchdog_pause.wait()
            update_cfg = []
            old_hash = dict(self._hash_dict)
            self._update_cfg_hash()
            for cfg in ("ecosystems", "private"):
                if old_hash[cfg] != self._hash_dict[cfg]:
                    update_cfg.append(cfg)
            if update_cfg:
                logger.info(f"Change in {cfg} config detected, updating it")
                self.update(update_cfg)
                with config_event:
                    config_event.notify_all()
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
        else:
            logger.debug("Configuration files watchdog is already running")

    def stop_watchdog(self) -> None:
        if self._started:
            logger.info("Stopping the configuration files watchdog")
            self._stop_event.set()
            self._thread.join()
            self._thread = None
            self._started = False
            logger.debug("Configuration files watchdog successfully stopped")

    def update(self, config: Union[tuple, list] = ("ecosystems", "private")) -> None:
        logger.debug("Updating configuration")
        self._watchdog_pause.clear()
        for cfg in config:
            self._load_config(cfg=cfg)
        self._update_cfg_hash()
        self._watchdog_pause.set()

    def save(self, cfg: str) -> None:
        file_path = self._base_dir/f"{cfg}.cfg"
        with lock:
            with open(file_path, "w") as file:
                if cfg == "ecosystems":
                    yaml.dump(self._ecosystems_config, file)
                elif cfg == "private":
                    yaml.dump(self._private_config, file)
                else:
                    raise ValueError("cfg should be 'ecosystems' or 'private'")

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

    def create_new_ecosystem(self, ecosystem_name: str) -> None:
        uid = self._create_new_ecosystem_uid()
        ecosystem_cfg = {uid: DEFAULT_ECOSYSTEM_CFG}
        ecosystem_cfg[uid]["name"] = ecosystem_name
        self._ecosystems_config.update(ecosystem_cfg)

    @property
    def as_dict(self) -> dict:
        return self._ecosystems_config

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
            if "home" in self._private_config["places"]:
                return self._private_config["places"]["home"]["city"]
            else:
                return "Somewhere over the rainbow"
        except Exception as ex:
            print(ex)
        return "Somewhere over the rainbow"

    @home_city.setter
    def home_city(self, city_name: str) -> None:
        home_city = {"places": {"home": {"city": city_name}}}
        self._private_config.update(home_city)
        self.save("private")

    @property
    def home_coordinates(self) -> dict:
        if "home" in self._private_config["places"]:
            try:
                return self._private_config["places"]["home"]["coordinates"]
            except KeyError:
                return get_coordinates(self.home_city)
        return {"latitude": 0, "longitude": 0}

    @home_coordinates.setter
    def home_coordinates(self, value: tuple) -> None:
        # value should be (latitude, longitude)
        coordinates = {"latitude": value[0], "longitude": value[1]}
        home = {"places": {"home": {"coordinates": coordinates}}}
        self._private_config.update(home)
        self.save("private")


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
    def as_dict(self) -> dict:
        return self._general_config.as_dict[self.uid]

    @property
    def name(self) -> str:
        return self.as_dict["name"]

    @name.setter
    def name(self, value: str) -> None:
        self.as_dict["name"] = value

    @property
    def status(self) -> bool:
        return self.as_dict["status"]

    @status.setter
    def status(self, value: bool) -> None:
        self.as_dict["status"] = value

    """Parameters related to sub-routines control"""
    def get_management(self, parameter: str) -> bool:
        try:
            return self.as_dict["management"].get(parameter, False)
        except (KeyError, AttributeError):
            return False

    def set_management(self, management: str, value: bool) -> None:
        self.as_dict["management"][management] = value

    def get_managed_subroutines(self) -> list:
        return [subroutine for subroutine in SUBROUTINES
                if self.get_management(subroutine)]

    """Environment related parameters"""
    @property
    def light_method(self) -> str:
        if not self.get_management("light"):
            return ""
        try:
            method = self.as_dict["environment"]["light"]
            if method in ("elongate", "mimic"):
                if not is_connected():
                    logger.warning(
                        "Not connected to the internet, light method "
                        "automatically turned to 'fixed'"
                    )
                    return "fixed"
            return method
        except KeyError:
            raise Exception("Either define ['environment']['light'] or remove "
                            "light management")

    @light_method.setter
    def light_method(self, method) -> None:
        self.as_dict["environment"]["light"] = method

    def get_chaos(self) -> dict:
        try:
            return self.as_dict["environment"]["chaos"]
        except KeyError:
            raise UndefinedParameter

    def get_climate_parameters(self, parameter: str) -> dict:
        try:
            data = {
                tod: self.as_dict["environment"][tod]["climate"][parameter]
                for tod in ("day", "night")
            }
        except KeyError:
            raise UndefinedParameter
        else:
            try:
                data["hysteresis"] = \
                    self.as_dict["environment"]["hysteresis"].get(parameter, 0)
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
        for t in ("day", "night"):
            self.as_dict["environment"][t]["target"] = value[t]

    """Parameters related to IO"""    
    @property
    def IO_dict(self) -> dict:
        """
        Returns the IOs (hardware) present in the ecosystem
        """
        return self.as_dict.get("IO", {})

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
        return [self.IO_dict[io]["address"]
                for io in self.IO_dict]

    def save(self):
        self._general_config.save("ecosystems")

    def create_new_hardware(
        self,
        name: str = "",
        address: str = "",
        model: str = "",
        _type: str = "",
        level: str = "",
        measure: list = [],
        plant: str = "",
        specific_type: str = "hardware",
    ) -> None:
        """
        Create a new hardware
        :param name: str, the name of the hardware to create
        :param address: str: the address of the hardware to create
        :param model: str: the name of the model of the hardware to create
        :param _type: str: the type of hardware to create ('sensor', 'light' ...)
        :param level: str: either 'environment' or 'plants'
        :param measure: list: the list of the measures taken
        :param plant: str: the name of the plant linked to the hardware
        :param specific_type: str: the type of hardware to create
        """
        assert address not in self._used_addresses(), \
            f"Address {address} already used"
        if specific_type.lower() == "gpio":
            h = gpioHardware
        elif specific_type.lower() == "i2c":
            h = i2cHardware
        else:
            h = Hardware
        uid = self._create_new_IO_uid()
        new_hardware = h(
            uid=uid,
            name=name,
            address=address,
            model=model,
            type=_type,
            level=level,
            measure=measure,
            plant=plant
        )
        self.IO_dict.update(new_hardware.dict_repr)
        self.save()

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

    """Parameters related to time"""
    @property
    def time_parameters(self) -> dict:
        try:
            parameters = {}
            day = self.as_dict["environment"]["day"]["start"]
            parameters["day"] = human_time_parser(day)
            night = self.as_dict["environment"]["night"]["start"]
            parameters["night"] = human_time_parser(night)
            return parameters
        except (KeyError, AttributeError):
            raise UndefinedParameter

    @time_parameters.setter
    def time_parameters(self, value: dict) -> None:
        if not isinstance(value, dict):
            raise ValueError("value should be a dict with keys equal to 'day' \
                             or 'night' and values equal to string representing \
                             a human readable time, such as '20h00'")
        self.as_dict["environment"]["day"]["start"] =\
            value["day"]["start"]
        self.as_dict["environment"]["night"]["start"] =\
            value["night"]["start"]
        self.save()

    @property
    def sun_times(self) -> dict:
        try:
            with open(self._general_config.base_dir/"cache/sunrise.json", "r") as file:
                sunrise = json.load(file)
        # TODO: handle when cache file does not exist
        except Exception:
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
