from datetime import date, datetime, time
import hashlib
import json
import logging
import pathlib
import random
import string
from threading import Thread, Event

import pytz
import ruamel.yaml
from tzlocal import get_localzone

from config import Config, base_dir
from engine.utils import get_coordinates, is_connected
from engine.hardware_library import hardware, gpioHardware, i2cHardware


new_config_event = Event()
localTZ = get_localzone()

logger = logging.getLogger("gaiaEngine.config")
# TODO: use a bidict for uid - name

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
_global_config = None


class _globalConfig:
    def __init__(self) -> None:
        logger.debug("Initializing globalConfig")
        self._ecosystems_config = None
        self.yaml = ruamel.yaml.YAML()
        self._load_config()

    def _load_config(self, **kwargs) -> None:
        cfg = kwargs.pop("cfg", ["ecosystems", "private"])
        if "ecosystems" in cfg:
            try:
                custom_cfg = base_dir / "ecosystems.cfg"
                with open(custom_cfg, "r") as file:
                    self._ecosystems_config = self.yaml.load(file)
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
                    self._private_config = self.yaml.load(file)
            except IOError:
                logger.warning("There is currently no custom private configuration file. "
                               "Using the default settings instead")
                self._private_config = {}

    def update(self, cfg: list = ["ecosystems", "private"]) -> None:
        logger.debug("Updating configuration")
        self._load_config(cfg=cfg)

    def save(self, cfg: str) -> None:
        if Config.TESTING:
            raise Exception("Config cannot be saved during testing")
        file_path = base_dir / f"{cfg}.cfg"
        with open(file_path, "w") as file:
            if cfg == "ecosystems":
                self.yaml.dump(self._ecosystems_config, file)
            elif cfg == "private":
                self.yaml.dump(self._private_config, file)

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

    def file_hash(self, file_path: pathlib.Path) -> hex:
        sha = hashlib.sha256()
        try:
            with open(file_path, 'rb') as f:
                fb = f.read(1024)
                while len(fb) > 0:
                    sha.update(fb)
                    fb = f.read(1024)
            h = sha.hexdigest()
            return h
        except FileNotFoundError:
            return None

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

    # TODO: use a bidict
    @property
    def id_to_name_dict(self) -> dict:
        return {ecosystem: self._ecosystems_config[ecosystem]["name"]
                for ecosystem in self._ecosystems_config}

    @property
    def name_to_id_dict(self) -> dict:
        return {self._ecosystems_config[ecosystem]["name"]: ecosystem
                for ecosystem in self._ecosystems_config}

    # TODO: use a named tuple
    def getIds(self, ecosystem: str) -> tuple:
        if ecosystem in self.ecosystems_id:
            ecosystem_id = ecosystem
            ecosystem_name = self.id_to_name_dict[ecosystem]
            return ecosystem_id, ecosystem_name
        elif ecosystem in self.ecosystems_name:
            ecosystem_id = self.name_to_id_dict[ecosystem]
            ecosystem_name = ecosystem
            return ecosystem_id, ecosystem_name
        raise ValueError("'ecosystem' parameter should either be an ecosystem " +
                         "id or an ecosystem name present in the ecosystems.cfg " +
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


class _configWatchdog:
    def __init__(self) -> None:
        logger.debug("Initializing configWatchdog")
        global _global_config
        if not _global_config:
            _global_config = _globalConfig()
        self._hash_dict = {}
        self._watchdog_stopEvent = Event()
        self._watchdogThread = None
        self._started = False

    def update_cfg_hash(self) -> None:
        for cfg in ("ecosystems", "private"):
            path = base_dir / f"{cfg}.cfg"
            self._hash_dict[cfg] = _global_config.file_hash(path)

    def _watchdog(self) -> None:
        update_cfg = []
        old_hash = self._hash_dict
        self.update_cfg_hash()
        for cfg in ("ecosystems", "private"):
            if old_hash[cfg] != self._hash_dict[cfg]:
                update_cfg.append(cfg)
        if update_cfg:
            _global_config.update(update_cfg)
            # set new_config_event, which is used by autoManager
            new_config_event.set()

    def _watchdog_loop(self) -> None:
        while not self._watchdog_stopEvent.is_set():
            self._watchdog()
            self._watchdog_stopEvent.wait(Config.CONFIG_WATCHER_PERIOD)

    """API calls"""
    def start(self) -> None:
        if not self._started:
            logger.info("Starting configWatchdog")
            self._watchdogThread = Thread(target=self._watchdog, args=())
            self._watchdogThread.name = "configWatchdog-Thread"
            self._watchdogThread.start()
            self._started = True
            logger.debug("configWatchdog successfully started")
        else:
            logger.debug("configWatchdog is already running")

    def stop(self) -> None:
        if self._started:
            logger.info("Stopping configWatchdog")
            self._watchdog_stopEvent.set()
            self._watchdogThread.join()
            self._watchdogThread = None
            self._started = False
            logger.debug("configWatchdog successfully stopping")

    @property
    def status(self) -> bool:
        return self._started


# ---------------------------------------------------------------------------
#   specificConfig class
# ---------------------------------------------------------------------------
class specificConfig:
    def __init__(self, ecosystem: str) -> None:
        global _global_config
        if not _global_config:
            _global_config = _globalConfig()
        ids = _global_config.getIds(ecosystem)
        logger.debug(f"Initializing specificConfig for ecosystem {ids[1]}")
        self.ecosystem_id = ids[0]
        self.config_dict = _global_config.config_dict[self.ecosystem_id]

    def __str__(self):
        return json.dumps(self.config_dict)

    @property
    def name(self) -> str:
        return self.config_dict["name"]

    @name.setter
    def name(self, value: str) -> None:
        self.config_dict["name"] = value
        _global_config.save("ecosystems")

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
    # TODO: rename to manage, with the new value added = False and
    #  if new value: update, else: return current value
    def get_management(self, parameter: str) -> bool:
        try:
            return self.config_dict["management"].get(parameter, False)
        except (KeyError, AttributeError):
            return False

    def set_management(self, parameter: str, value: bool) -> None:
        self.config_dict["management"][parameter] = value

    # TODO: keep up to date with subroutines.NAME
    def get_started_subroutines(self) -> list:
        subroutines = ("light", "sensors", "health", "climate")
        return [subroutine for subroutine in subroutines
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
    def chaos(self) -> str:
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
        _global_config.save(cfg)

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

            self.IO_dict.update(new_hardware.dict_repr())
            if not any((Config.DEBUG, Config.TESTING)):
                _global_config.save("ecosystems")

            response["status"] = "200"
            response["message"] = f"Hardware {name} successfully created"
            response["hardware_info"] = new_hardware.dict_repr()
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
            if not any((Config.DEBUG, Config.TESTING)):
                _global_config.save("ecosystems")
            return

        if name:
            _uid = [uid for uid in self.IO_dict
                    if self.IO_dict[uid]["name"] == name]
            del self.IO_dict[_uid]
            if not any((Config.DEBUG, Config.TESTING)):
                _global_config.save("ecosystems")

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
        _global_config.save("ecosystems")

    def utc_time_to_local_time(self, utc_time: time) -> time:
        dt = datetime.combine(date.today(), utc_time)
        local_dt = pytz.utc.localize(dt)
        local_time = local_dt.astimezone(localTZ).time()
        return local_time

    @property
    def sun_times(self) -> dict:
        try:
            with open(base_dir /"cache/sunrise.json", "r") as file:
                sunrise = _global_config.yaml.load(file)
        # TODO: handle when cache file does not exist
        except:
            return

        def import_daytime_event(daytime_event: str) -> time:
            try:
                mytime = datetime.strptime(sunrise[daytime_event], "%I:%M:%S %p").time()
                local_time = self.utc_time_to_local_time(mytime)
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
#   Config manager class
# ---------------------------------------------------------------------------
class _configManager:
    """
    This class should not be manually instantiated. It is automatically
    instantiated once if needed and is used to store and return unique
    specificConfig objects. This way, whenever using get_config('my_ecosystem'),
    it will return the same object.
    """
    def __init__(self) -> None:
        logger.debug("Initializing configManager")
        self.configs = {}
        global _global_config
        if not _global_config:
            _global_config = _globalConfig()

    def get_config(self, ecosystem: str = None):
        if not ecosystem:
            return _global_config
        ecosystem_id, ecosystem_name = _global_config.getIds(ecosystem)
        try:
            return self.configs[ecosystem_id]
        except KeyError:
            cfg = specificConfig(ecosystem_id)
            self.configs[ecosystem_id] = cfg
            return cfg


# ---------------------------------------------------------------------------
#   Functions to interact with the module
# ---------------------------------------------------------------------------
_config_watchdog = None
_config_manager = None


class configWatchdog:
    """ Dummy class to interact with _configWatchdog()

    This class will instantiate _configWatchdog() if needed and allow to interact
    with it. This allows not to launch an instance of _configWatchdog when loading
    this module for testing purpose or when making custom scripts.
    """

    @staticmethod
    def start() -> None:
        global _config_watchdog
        if not _config_watchdog:
            _config_watchdog = _configWatchdog()
        _config_watchdog.start()

    @staticmethod
    def stop() -> None:
        global _config_watchdog
        if _config_watchdog:
            _config_watchdog.stop()

    @staticmethod
    def status() -> bool:
        global _config_watchdog
        if not _config_watchdog:
            return False
        return _config_watchdog.status

    @staticmethod
    def is_init() -> bool:
        global _config_watchdog
        if not _config_watchdog:
            return False
        return True

def getIds(ecosystem: str) -> tuple:
    """Return the tuple (ecosystem_uid, ecosystem_name)

    :param ecosystem: str, either an ecosystem uid or ecosystem name
    """
    global _global_config
    if not _global_config:
        _global_config = _globalConfig()
    return _global_config.getIds(ecosystem)


def updateConfig() -> None:
    """Update the globalConfig based on ecosystem.cfg and private.cfg"""
    global _global_config
    if not _global_config:
        _global_config = _globalConfig()
    _global_config.update()


def createEcosystem(*args) -> None:
    """Create a new ecosystem with the given name"""
    global _global_config
    if not _global_config:
        _global_config = _globalConfig()
    if len(args) == 0:
        name = input("Ecosystem name: ")
    else:
        name = args[0]
    _global_config.create_new_ecosystem(name)


def manageEcosystem() -> None:
    pass


def delEcosystem() -> None:
    pass


def getConfig(ecosystem: str = None):
    """ Return the specificConfig object for the given ecosystem.

    If no ecosystem is provided, return the globalConfig object instead

    :param ecosystem: str, and ecosystem uid or name. If left to none, will
                      return globalConfig object instead.
    """

    global _config_manager
    if not _config_manager:
        _config_manager = _configManager()
    return _config_manager.get_config(ecosystem)


def delConfig(ecosystem: str) -> None:
    global _config_manager
    if not _config_manager:
        _config_manager = _configManager()
    ecosystem_uid = getIds(ecosystem)[0]
    del _config_manager.configs[ecosystem_uid]
