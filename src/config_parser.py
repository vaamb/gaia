from datetime import datetime, time
import json
import logging
import random
import string
from threading import Condition, Event, Lock, Thread
from typing import Union

from config import Config
from src.utils import base_dir, file_hash, get_coordinates, is_connected, \
    SingletonMeta, utc_time_to_local_time, yaml
from src.hardware.base import hardware, gpioHardware, i2cHardware


SUBROUTINE_NAMES = ("climate", "health", "light", "sensors")


config_event = Condition()
lock = Lock()


logger = logging.getLogger("gaiaEngine.config")


# ---------------------------------------------------------------------------
#   default ecosystem configuration
# ---------------------------------------------------------------------------
DEFAULT_ECOSYSTEM_CFG = {
    "default": {
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
        "environment": {
            "chaos": 20,
            "light": "fixed",
            "day": {
                "start": "8h00",
                "temperature": 22,
                "humidity":  70,
            },
            "night": {
                "start": "20h00",
                "temperature": 17,
                "humidity": 40,
            },
            "hysteresis": {
                "temperature": 2,
                "humidity": 5,
            },
        },
    },
}


# ---------------------------------------------------------------------------
#   _globalConfig class
# ---------------------------------------------------------------------------
class GeneralConfig(metaclass=SingletonMeta):
    def __init__(self) -> None:
        logger.debug("Initializing GeneralConfig")
        self._ecosystems_config: dict = {}
        self._private_config: dict = {}
        self._load_config()
        self._hash_dict = {}
        self._stop_event = Event()
        self._thread = None
        self._started = False
        self._watchdog_pause = False
        _configs["__general__"] = self

    def _load_config(self, **kwargs) -> None:
        cfg = kwargs.pop("cfg", ("ecosystems", "private"))
        if "ecosystems" in cfg:
            try:
                custom_cfg = base_dir/"ecosystems.cfg"
                with open(custom_cfg, "r") as file:
                    self._ecosystems_config = yaml.load(file)
                    if self._ecosystems_config == DEFAULT_ECOSYSTEM_CFG:
                        self.default = True
                    else:
                        self.default = False
            except IOError:
                logger.warning("There is currently no custom ecosystem configuration file. "
                               "Using the default configuration instead")
                # create a new ecosystem, which is loaded as self._ecosystems_config
                self._ecosystems_config = {}
                self.create_new_ecosystem("Default Ecosystem")
                self.default = True

        if "private" in cfg:
            # Try to import custom private configuration file. If it doesn't exist, use default
            try:
                private_cfg = base_dir / "private.cfg"
                with open(private_cfg, "r") as file:
                    self._private_config = yaml.load(file)
            except IOError:
                logger.warning("There is currently no custom private configuration file. "
                               "Using the default settings instead")
                self._private_config = {}

    def update_cfg_hash(self) -> None:
        for cfg in ("ecosystems", "private"):
            path = base_dir / f"{cfg}.cfg"
            self._hash_dict[cfg] = file_hash(path)

    def _watchdog_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self._watchdog_pause:
                update_cfg = []
                old_hash = self._hash_dict
                self.update_cfg_hash()
                for cfg in ("ecosystems", "private"):
                    if old_hash[cfg] != self._hash_dict[cfg]:
                        update_cfg.append(cfg)
                if update_cfg:
                    logger.info("Change in config detected, updating it")
                    self.update(update_cfg)
                    # set config_event, which is used by autoManager
                    with config_event:
                        config_event.notify_all()
            self._stop_event.wait(Config.CONFIG_WATCHER_PERIOD)

    def start_watchdog(self) -> None:
        if not self._started:
            logger.info("Starting configWatchdog")
            self.update_cfg_hash()
            self._thread = Thread(target=self._watchdog_loop)
            self._thread.name = "config_watchdog-Thread"
            self._thread.start()
            self._started = True
            logger.debug("configWatchdog successfully started")
        else:
            logger.debug("configWatchdog is already running")

    def stop_watchdog(self) -> None:
        if self._started:
            logger.info("Stopping configWatchdog")
            self._stop_event.set()
            self._thread.join()
            self._thread = None
            self._started = False
            logger.debug("configWatchdog successfully stopping")

    @property
    def watchdog_status(self) -> bool:
        return self._started

    def update(self, cfg: Union[tuple, list] = ("ecosystems", "private")) -> None:
        logger.debug("Updating configuration")
        self._load_config(cfg=cfg)

    def save(self, cfg: str) -> None:
        if not any((Config.DEBUG, Config.TESTING)):
            raise Exception("Config cannot be saved during testing")
        file_path = base_dir/f"{cfg}.cfg"
        with open(file_path, "w") as file:
            if cfg == "ecosystems":
                with lock:
                    self.dump(self._ecosystems_config, file)
            if cfg == "private":
                with lock:
                    self.dump(self._private_config, file)

    def create_new_ecosystem_id(self) -> str:
        length = 8
        used_ids = self.ecosystems_id
        while True:
            x = random.choice(string.ascii_letters) + "".join(
                random.choices(string.ascii_letters + string.digits,
                               k=length-1))
            if x not in used_ids:
                break
        return x

    # TODO: finish
    def create_new_ecosystem(self, ecosystem_name: str) -> None:
        new_ecosystem_cfg = DEFAULT_ECOSYSTEM_CFG
        new_id = self.create_new_ecosystem_id()
        old_id = list(new_ecosystem_cfg.keys())[0]
        new_ecosystem_cfg[new_id] = new_ecosystem_cfg.pop(old_id)
        new_ecosystem_cfg[new_id]["name"] = ecosystem_name
        """
        self._ecosystems_config.update(new_ecosystem_cfg)
        self.save("ecosystems")
        """

    @property
    def config_dict(self) -> dict:
        return self._ecosystems_config

    @config_dict.setter
    def config_dict(self, dct: dict):
        self._ecosystems_config = dct

    @property
    def ecosystems_id(self) -> list:
        return [i for i in self._ecosystems_config]

    @property
    def ecosystems_name(self) -> list:
        return [self._ecosystems_config[i]["name"]
                for i in self._ecosystems_config]

    def status(self, ecosystem_id: str) -> bool:
        return self._ecosystems_config[ecosystem_id]["status"]

    def set_status(self, ecosystem_id: str, value: bool) -> None:
        self._ecosystems_config[ecosystem_id]["status"] = value

    @property
    def id_to_name_dict(self) -> dict:
        return {ecosystem: self._ecosystems_config[ecosystem]["name"]
                for ecosystem in self._ecosystems_config}

    @property
    def name_to_id_dict(self) -> dict:
        return {self._ecosystems_config[ecosystem]["name"]: ecosystem
                for ecosystem in self._ecosystems_config}

    # TODO: use a named tuple
    def get_IDs(self, ecosystem: str) -> tuple:
        if ecosystem in self.ecosystems_id:
            ecosystem_id = ecosystem
            ecosystem_name = self.id_to_name_dict[ecosystem]
            return ecosystem_id, ecosystem_name
        elif ecosystem in self.ecosystems_name:
            ecosystem_id = self.name_to_id_dict[ecosystem]
            ecosystem_name = ecosystem
            return ecosystem_id, ecosystem_name
        raise ValueError("'ecosystem' parameter should either be an ecosystem " +
                         "uid or an ecosystem name present in the ecosystems.cfg " +
                         "file. If you want to create a new ecosystem configuration " +
                         "use the function 'createConfig()'.")

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


# ---------------------------------------------------------------------------
#   specificConfig class
# ---------------------------------------------------------------------------
class SpecificConfig:
    def __init__(self, ecosystem: str) -> None:
        self.general_config = get_general_config()
        ids = self.general_config.get_IDs(ecosystem)
        logger.debug(f"Initializing specificConfig for ecosystem {ids[1]}")
        self.ecosystem_id = ids[0]
        self.config_dict = self.general_config.config_dict[self.ecosystem_id]

    def __str__(self):
        return json.dumps(self.config_dict)

    @property
    def name(self) -> str:
        return self.config_dict["name"]

    @name.setter
    def name(self, value: str) -> None:
        self.config_dict["name"] = value
        self.general_config.save("ecosystems")

    @property
    def uid(self) -> str:
        return self.ecosystem_id

    @property
    def status(self) -> bool:
        return self.config_dict["status"]

    @status.setter
    def status(self, value: bool) -> None:
        self.config_dict["status"] = value

    """Parameters related to sub-routines control"""
    def get_management(self, parameter: str) -> bool:
        try:
            return self.config_dict["management"].get(parameter, False)
        except (KeyError, AttributeError):
            return False

    def set_management(self, parameter: str, value: bool) -> None:
        self.config_dict["management"][parameter] = value

    # TODO: keep up to date with subroutines.NAME
    def get_managed_subroutines(self) -> list:
        return [subroutine for subroutine in SUBROUTINE_NAMES
                if self.get_management(subroutine)]

    """Environment related parameters"""
    @property
    def light_method(self) -> str:
        if not self.get_management("light"):
            return None
        try:
            method = self.config_dict["environment"]["light"]
            if method in ("elongate", "mimic"):
                if not is_connected():
                    logger.warning("Not connected to the internet, light "
                                   "method automatically turned to 'fixed'")
                    return "fixed"
            return method
        except KeyError:
            raise Exception("Either define ['environment']['light'] or remove "
                            "light management")

    @property
    def chaos(self) -> int:
        try:
            return self.config_dict["environment"]["chaos"]
        except KeyError:
            return 0

    def get_climate_parameters(self, parameter: str) -> dict:
        if parameter not in ("temperature", "humidity"):
            raise ValueError("parameter should be 'temperature' or 'humidity'")
        data = {}
        for moment_of_day in ("day", "night"):
            try:
                data[moment_of_day] = \
                    self.config_dict["environment"][moment_of_day].get(
                        parameter, None)
            except KeyError:
                data[moment_of_day] = None
        try:
            data["hysteresis"] = \
                self.config_dict["environment"]["hysteresis"].get(
                    parameter, None)
        except KeyError:
            data["hysteresis"] = 0
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
            self.config_dict["environment"][t]["target"] = value[t]
            # TODO: check hysteresis

    """Parameters related to IO"""    
    @property
    def IO_dict(self) -> dict:
        """
        Returns the IOs (hardware) present in the ecosystem
        """
        return self.config_dict.get("IO", {})

    def get_IO_group(self,
                     IO_type: str,
                     level: tuple = ("environment", "plants")
                     ) -> list:
        return [uid for uid in self.IO_dict
                if self.IO_dict[uid]["type"].lower() == IO_type
                and self.IO_dict[uid]["level"].lower() in level]

    def get_lights(self) -> list:
        return [uid for uid in self.IO_dict
                if self.IO_dict[uid]["type"].lower() == "light"]

    def get_sensors(self) -> list:
        return [uid for uid in self.IO_dict
                if self.IO_dict[uid]["type"].lower() == "sensor"]

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

    def save(self, cfg):
        if not any((Config.DEBUG, Config.TESTING)):
            self.general_config.save(cfg)

    def create_new_hardware(self,
                            name: str = "",
                            address: str = "",
                            model: str = "",
                            _type: str = "",
                            level: str = "",
                            measure: list = [],
                            plant: str = "",
                            specific_type: str = "hardware",
                            ) -> dict:
        response = {}
        try:
            assert address not in self._used_addresses(), \
                f"Address {address} already used"
            if specific_type.lower() == "gpio":
                h = gpioHardware
            elif specific_type.lower() == "i2c":
                h = i2cHardware
            else:
                h = hardware
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
            self.save("ecosystems")

            response["status"] = "200"
            response["message"] = f"Hardware {name} successfully created"
            response["hardware_info"] = new_hardware.dict_repr
        except Exception as e:
            response["status"] = "400"
            response["message"] = e
            response["hardware_info"] = None
        return response

    def create_new_GPIO_hardware(self, **kwargs) -> dict:
        """
        Create a new GPIO hardware
        :param name: str, the name of the hardware to create
        :param address: str: the address of the hardware to create
        :param model: str: the name of the model of the hardware to create
        :param _type: str: the type of hardware to create ('sensor', 'light' ...)
        :param level: str: either 'environment' or 'plants'
        """
        kwargs["specific_type"] = "GPIO"
        return self.create_new_hardware(**kwargs)

    def create_new_GPIO_sensor(self, **kwargs) -> dict:
        """
        Create a new GPIO sensor
        :param name: str, the name of the hardware to create
        :param address: str: the address of the hardware to create
        :param model: str: the name of the model of the hardware to create
        :param level: str: either 'environment' or 'plants'
        """
        kwargs["_type"] = "sensor"
        return self.create_new_GPIO_hardware(**kwargs)

    def create_new_I2C_hardware(self, **kwargs) -> dict:
        """
        Create a new I2C hardware
        :param name: str, the name of the hardware to create
        :param address: str: the address of the hardware to create
        :param model: str: the name of the model of the hardware to create
        :param _type: str: the type of hardware  to create ('sensor', 'light' ...)
        :param level: str: either 'environment' or 'plants'
        """
        kwargs["specific_type"] = "I2C"
        return self.create_new_hardware(**kwargs)

    def create_new_I2C_sensor(self, **kwargs) -> dict:
        """
        Create a new I2C sensor hardware
        :param name: str, the name of the hardware to create
        :param address: str: the address of the hardware to create
        :param model: str: the name of the model of the hardware to create
        :param level: str: either 'environment' or 'plants'
        """
        kwargs["type"] = "sensor"
        return self.create_new_I2C_hardware(**kwargs)

    def delete_hardware(self, uid=None, name=None) -> None:
        """
        Delete a hardware from the config
        :param uid: str, the uid of the hardware to delete
        :param name: str, the name of the hardware to delete

        Rem: prefer to use uid as it is unique for each object
        """
        assert uid or name, "You need to provide at least the uid or the name " \
                            "of the hardware to delete"
        if uid and name:
            assert self.IO_dict[uid]["name"] == name, "name and uid do not refer" \
                                                      " to the same hardware"
        if uid:
            del self.IO_dict[uid]
            self.save("ecosystems")
            return

        if name:
            _uid = [uid for uid in self.IO_dict
                    if self.IO_dict[uid]["name"] == name]
            del self.IO_dict[_uid]
            self.save("ecosystems")

    """Parameters related to time"""
    def human_time_parser(self, human_time: str) -> time:
        """
        Returns the time from config file written in a human readable manner
        as a datetime.time object
        
        :param human_time: str, the time written in a 24h format, with hours
        and minutes separated by a 'h' or a 'H'. 06h05 as well as 6h05 or 
        even 6H5 are valid input
        """
        hours, minutes = human_time.replace('H', 'h').split("h")
        return time(int(hours), int(minutes))

    @property
    def time_parameters(self) -> dict:
        parameters = {
            "day": None,
            "night": None,
        }
        try:
            day = self.config_dict["environment"]["day"]["start"]
            parameters["day"] = self.human_time_parser(day)
            night = self.config_dict["environment"]["night"]["start"]
            parameters["night"] = self.human_time_parser(night)
        except (KeyError, AttributeError):
            pass
        return parameters

    @time_parameters.setter
    def time_parameters(self, value: dict) -> None:
        if not isinstance(value, dict):
            raise ValueError("value should be a dict with keys equal to 'day' \
                             or 'night' and values equal to string representing \
                             a human readable time, such as '20h00'")
        self.config_dict["environment"]["day"]["start"] =\
            value["day"]["start"]
        self.config_dict["environment"]["night"]["start"] =\
            value["night"]["start"]
        self.save("ecosystems")

    @property
    def sun_times(self) -> dict:
        try:
            with open(base_dir/"cache/sunrise.json", "r") as file:
                sunrise = json.load(file)
        # TODO: handle when cache file does not exist
        except:
            return {}

        def import_daytime_event(daytime_event: str) -> time:
            try:
                mytime = datetime.strptime(sunrise[daytime_event], "%I:%M:%S %p").time()
                local_time = utc_time_to_local_time(mytime)
                return local_time
            except Exception as ex:
                print(ex)
            return None
        return {
            "twilight_begin": import_daytime_event(
                "civil_twilight_begin") or time(8, 00),
            "sunrise": import_daytime_event("sunrise") or time(8, 00),
            "sunset": import_daytime_event("sunset") or time(20, 00),
            "twilight_end": import_daytime_event(
                "civil_twilight_end") or time(20, 00),
        }


# ---------------------------------------------------------------------------
#   Functions to interact with the module
# ---------------------------------------------------------------------------
_configs = {}


def get_general_config() -> GeneralConfig:
    try:
        return _configs["__general__"]
    except KeyError:
        _configs["__general__"] = GeneralConfig()
        return _configs["__general__"]


def get_config(ecosystem: str = None) -> Union[GeneralConfig, SpecificConfig]:
    """ Return the specificConfig object for the given ecosystem.

    If no ecosystem is provided, return the globalConfig object instead

    :param ecosystem: str, an ecosystem uid or name. If left to none, will
                      return globalConfig object instead.
    """
    global_cfg = get_general_config()

    if not ecosystem:
        return global_cfg

    ecosystem_id, ecosystem_name = global_cfg.get_IDs(ecosystem)
    try:
        return _configs[ecosystem_id]
    except KeyError:
        _configs[ecosystem_id] = SpecificConfig(ecosystem_id)
        return _configs[ecosystem_id]


def get_IDs(ecosystem: str) -> tuple:
    """Return the tuple (ecosystem_uid, ecosystem_name)

    :param ecosystem: str, either an ecosystem uid or ecosystem name
    """
    return get_general_config().get_IDs(ecosystem)


def update_config() -> None:
    """Update the globalConfig based on ecosystem.cfg and private.cfg"""
    get_general_config().update()


def detach_config(ecosystem) -> None:
    UID = get_general_config().get_IDs(ecosystem)[0]
    del _configs[UID]


def createEcosystem(name) -> None:
    """Create a new ecosystem with the given name"""
    get_general_config().create_new_ecosystem(name)


def manageEcosystem() -> None:
    pass


def delEcosystem() -> None:
    pass
