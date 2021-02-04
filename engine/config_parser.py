from datetime import date, time, datetime
import hashlib
import logging
import pathlib
import random
import socket
import string
from threading import Thread, Event

import pytz
import ruamel.yaml
from tzlocal import get_localzone

from config import Config


gaiaEngine_dir = pathlib.Path(__file__).absolute().parents[1]
new_config_event = Event()
localTZ = get_localzone()


logger = logging.getLogger("config")


def is_connected():
    try:
        host = socket.gethostbyname(Config.TEST_CONNECTION_IP)
        s = socket.create_connection((host, 80), 2)
        s.close()
        return True
    except Exception as ex:
        print(ex)
    return False


def str_to_bool(s: str):
    if s.lower() == "true":
        return True
    elif s.lower() == "false":
        return False
    else:
        raise ValueError(f"{s} can either be 'True'/'true' or 'False'/'false'")


#---------------------------------------------------------------------------
#   _globalConfig class
#---------------------------------------------------------------------------
class _globalConfig:
    def __init__(self) -> None:
        self._ecosystems_config = None
        self.yaml = ruamel.yaml.YAML()
        self._load_config()
        self.watchdog = False
        self._internal_change = False

    def _load_config(self) -> None:
        try:
            custom_cfg = gaiaEngine_dir/"ecosystems.cfg"
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

        # Try to import custom private configuration file. If it doesn't exist, use default
        try:
            private_cfg = gaiaEngine_dir/"private.cfg"
            with open(private_cfg, "r") as file:
                self._private_config = self.yaml.load(file)
        except IOError:
            logger.warning("There is currently no custom private configuration file. "
                           "Using the default settings instead")
            self._private_config = {}

    def update(self) -> None:
        logger.debug("Updating configuration")
        self._load_config()

    def save(self, cfg: str) -> None:
        file_path = gaiaEngine_dir/f"{cfg}.cfg"
        with open(file_path, "w") as file:
            if cfg == "ecosystems":
                self.yaml.dump(self._ecosystems_config, file)
            elif cfg == "private":
                self.yaml.dump(self._private_config)

    def create_new_ecosystem_id(self) -> str:
        k = 8
        used_ids = self.ecosystems_id
        while True:
            x = "".join(random.choices(string.ascii_letters + string.digits, k=k))
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

    def _watchdog(self) -> None:
        self._hash_dict = {}
        for cfg in ("ecosystems", "private"):
            path = gaiaEngine_dir/f"{cfg}.cfg"
            self._hash_dict[cfg] = self.file_hash(path)        
        while not self._watchdog_stopEvent.is_set():
            update_cfg = False
            for cfg in ("ecosystems", "private"):
                old_hash = self._hash_dict[cfg]
                file_path = gaiaEngine_dir/f"{cfg}.cfg"
                self._hash_dict[cfg] = self.file_hash(file_path)
                if old_hash != self._hash_dict[cfg]:
                    update_cfg = True
            if update_cfg and not self._internal_change:
                self.update()
                new_config_event.set()
            self._watchdog_stopEvent.wait(Config.CONFIG_WATCHER_PERIOD)

    """API calls"""
    def start_watchdog(self) -> None:
        if not self.watchdog:
            self._watchdog_stopEvent = Event()
            self._watchdogThread = Thread(target=self._watchdog, args=())
            self._watchdogThread.name = "configWatchdog"
            self._watchdogThread.start()
            self.watchdog = True
        else:
            logger.debug("Config watchdog is already running")

    def stop_watchdog(self) -> None:
        if self.watchdog:
            self._watchdog_stopEvent.set()
            self._watchdogThread.join()
            del self._watchdogThread, self._watchdog_stopEvent
            self.watchdog = False

    @property
    def config_dict(self) -> dict:
        return self._ecosystems_config

    @config_dict.setter
    def config_dict(self, dct: dict):
        self._ecosystems_config = dct

    @property
    def ecosystems_id(self) -> list:
        return [i for i  in self._ecosystems_config]

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
    def name_to_id_dict(self) -> None:
        return {self._ecosystems_config[ecosystem]["name"]: ecosystem
                for ecosystem in self._ecosystems_config}

    """Private config parameters"""
    # TODO: use geopy with memoization
    @property
    def home_coordinates(self) -> dict:
        try:
            if "home" in self._private_config["places"]:
                return self._private_config["places"]["home"]["coordinates"]
            else:
                return {"latitude": 0, "longitude": 0}
        except Exception as ex:
            print(ex)
        return {"latitude": 0, "longitude": 0}

    @home_coordinates.setter
    def home_coordinates(self, value: tuple) -> None:
        # value should be (latitude, longitude)
        coordinates = {"latitude": value[0], "longitude": value[1]}
        home = {"places": {"home": {"coordinates": coordinates}}}
        self._private_config.update(home)

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


DEFAULT_ECOSYSTEM_CFG = {
    "default": {
        "name": "",
        "status": False,
        "management": {
            "sensors": True,
            "light": False,
            "watering": False,
            "climate": False,
            "health": False,
            "alarms": False,
        },
        "webcam": {
            "status": False,
            "model": "regular",
        },
        "environment": {
            "chaos": 20,
            "light": "fixed",
            "day": {
                "start": "8h00",
                "temperature": {
                    "target": 22,
                },

                "humidity": {
                    "target": 70,
                },
            },
            "night": {
                "start": "20h00",
                "temperature": {
                    "target": 17,
                },

                "humidity": {
                    "target": 40,
                },
            },
            "hysteresis": {
                "temperature": 2,
                "humidity": 5,
            },
        },
    },
}


globalConfig = _globalConfig()


#---------------------------------------------------------------------------
#   specificConfig class
#---------------------------------------------------------------------------
class specificConfig:
    def __init__(self, ecosystem: str) -> None:
        if ecosystem in globalConfig.ecosystems_id:
            self.ecosystem_id = ecosystem
        elif ecosystem in globalConfig.ecosystems_name:
            self.ecosystem_id = globalConfig.name_to_id_dict[ecosystem]
        else:
            raise ValueError("Please provide either a valid ecosystem id or "
                             "a valid ecosystem name.")
        self.config_dict = globalConfig.config_dict[self.ecosystem_id]
    # config_dict is passed after globalConfig is instanciated
    # specificConfig.config_dict = globalConfig.config_dict

    @property
    def name(self) -> str:
        return self.config_dict["name"]

    @name.setter
    def name(self, value: str) -> None:
        self.config_dict["name"] = value
        globalConfig.save("ecosystems")

    @property
    def uid(self) -> str:
        return self.ecosystem_id

    @property
    def status(self) -> bool:
        return self.config_dict["status"]

    @status.setter
    def status(self, value: bool) -> None:
        self.config_dict["status"] = value

    """Parameters related to sub-processes control"""
    def get_management(self, parameter: str) -> bool:
        try:
            return self.config_dict["management"][parameter]
        except Exception as ex:
            print(ex)
            return False

    def set_management(self, parameter: str, value: bool) -> None:
        self.config_dict["controls"][parameter] = value

    """Environment related parameters"""
    @property
    def light_method(self) -> str:
        if not is_connected():
            logger.warning("Not connected to the internet, light method automatically turned to 'fixed'")
            return "fixed"
        else:
            return self.config_dict["environment"]["light"]

    @property
    def chaos(self) -> str:
        try:
            return self.config_dict["environment"]["chaos"]
        except Exception:
            raise AttributeError("Chaos was not configure for {}".format(self.name))

    def get_climate_parameters(self, parameter: str) -> dict:
        if parameter not in ("temperature", "humidity"):
            raise ValueError("parameter should be set to either 'temperature' or 'humidity'")
        data = {"hysteresis": self.config_dict["environment"]["hysteresis"]}
        for moment_of_day in ("day", "night"):
            data[moment_of_day] = self.config_dict["environment"][moment_of_day]["target"]
        return data

    def set_climate_parameters(self, parameter: str, value: dict) -> None:
        if parameter not in ("temperature", "humidity"):
            raise ValueError("parameter should be set to either 'temperature' or 'humidity'")
        if not isinstance(value, dict):
            raise ValueError("value should be a dict with keys equal to 'day' \
                             or 'night' and values equal to the required \
                             parameter")
        for t in ("day", "night"):
            self.config_dict["environment"][t]["target"] = value[t]

    """Parameters related to IO"""    
    @property
    def IO_dict(self) -> dict:
        """
        Returns the IO present in the ecosystem under the form of a dict
        """
        return self.config_dict.get("IO", {})

    def get_IO_group(self,
                     IO_type: str,
                     level: list = ["environment", "plants"]
                     ) -> list:
        return [IO for IO in self.IO_dict
                if self.IO_dict[IO]["type"] == IO_type
                and self.IO_dict[IO]["level"] in level]

    def get_lights(self) -> list:
        return [IO for IO in self.IO_dict
                if self.IO_dict[IO]["type"] == "light"]

    def get_sensors(self) -> list:
        return [IO for IO in self.IO_dict
                if self.IO_dict[IO]["type"] == "sensor"]

    def create_new_IO_id(self) -> str:
        k = 16
        used_ids = list(self.IO_dict.keys())
        while True:
            x = "".join(random.choices(string.ascii_letters + string.digits, k=k))
            if x not in used_ids:
                break
        return x

    # TODO: take changes from pin to address into account
    def _create_new_IO(self,
                       name: str,
                       address: str,
                       IO_type: str,
                       IO_level: str,
                       model: str,
                       measure: list,
                       plant: str = ""
                       ) -> None:
        used_pins = []
        for id in self.config_dict:
            for io in self.config_dict[id]["IO"]:
                # need to check pins for all! ecosystems
                address = self.config_dict[id]["IO"][io].get("address", None)
                if address:
                    used_pins.append(address)
        assert address not in used_pins, f"Pin {address} already used"
        uid = self.create_new_IO_id()
        if IO_level != "plant":
            new_IO = {
                uid: {
                    "name": name,
                    "address": address,
                    "type": IO_type,
                    "level": IO_level,
                    "model": model,
                    "measure": measure,
                    }
                }
        else:
            assert plant != "", "You need to provide a plant name"
            new_IO = {
                uid: {
                    "name": name,
                    "pin": address,
                    "type": IO_type,
                    "level": IO_level,
                    "model": model,
                    "measure": measure,
                    "plant": plant
                    }
                }
        self.IO_dict.update(new_IO)
        globalConfig.save("ecosystems")

    def create_new_GPIO_sensor(self):
        # TODO: same as new IO but auto generate address
        pass

    """Parameters related to time"""
    def human_time_parser(self, human_time: str) -> time:
        """
        Returns the time from config file written in a human readable manner
        as a datetime.time object
        
        :param human_time: str, the time written in a 24h format, with hours
        and minutes separated by a 'h' or a 'H'. 06h05 as well as 6h05 or 
        even 6H5 are valid input
        """
        hours, minutes = human_time.replace('H','h').split("h")
        return time(int(hours), int(minutes))

    @property
    def time_parameters(self) -> dict:
        try:
            t = {}
            day = self.config_dict["environment"]["day"]["start"]
            t["day"] = self.human_time_parser(day)
            night = self.config_dict["environment"]["night"]["start"]
            t["night"] = self.human_time_parser(night)
            return t
        except:
            raise AttributeError("No time parameter set")

    @time_parameters.setter
    def time_parameters(self, value: dict) -> None:
        if not isinstance(value, dict):
            raise ValueError("value should be a dict with keys equal to 'day' \
                             or 'night' and values equal to strinf representing \
                             a human readable time, such as '20h00'")
        self.config_dict["environment"]["day"]["start"] =\
            value["day"]["start"]
        self.config_dict["environment"]["night"]["start"] =\
            value["night"]["start"]
        globalConfig.save("ecosystems")

    def utc_time_to_local_time(self, utc_time: time) -> time:
        dt = datetime.combine(date.today(), utc_time)
        local_dt = pytz.utc.localize(dt)
        local_time = local_dt.astimezone(localTZ).time()
        return local_time

    @property
    def moments(self) -> dict:
        with open(gaiaEngine_dir/"cache/sunrise.cch", "r") as file:
            sunrise = globalConfig.yaml.load(file)

        def import_daytime_event(daytime_event: str) -> time:
            try:
                mytime = datetime.strptime(sunrise[daytime_event], "%I:%M:%S %p").time()
                local_time = self.utc_time_to_local_time(mytime)
                return local_time
            except Exception as ex:
                print(ex)
            return None
        moments = {}
        moments["twilight_begin"] = import_daytime_event("civil_twilight_begin") or time(8, 00)
        moments["sunrise"] = import_daytime_event("sunrise") or time(8, 00)
        moments["sunset"] = import_daytime_event("sunset") or time(20, 00)
        moments["twilight_end"] = import_daytime_event("civil_twilight_end") or time(20, 00)
        return moments


#---------------------------------------------------------------------------
#   Manager class
#---------------------------------------------------------------------------
class Manager:
    def __init__(self) -> None:
        self.configs = {}

    def getIds(self, ecosystem: str) -> tuple:
        if ecosystem in globalConfig.ecosystems_id:
            ecosystem_id = ecosystem
            ecosystem_name = globalConfig.id_to_name_dict[ecosystem]
            return ecosystem_id, ecosystem_name
        elif ecosystem in globalConfig.ecosystems_name:
            ecosystem_id = globalConfig.name_to_id_dict[ecosystem]
            ecosystem_name = ecosystem
            return ecosystem_id, ecosystem_name
        raise ValueError("'ecosystem' parameter should either be an ecosystem " +
                         "id or an ecosystem name present in the ecosystems.cfg " +
                         "file. If you want to create a new ecosystem configuration " +
                         "use the function 'createConfig()'.")

    def get_ecosystem_config(self, ecosystem: str) -> specificConfig:
        ecosystem_id, ecosystem_name = self.getIds(ecosystem)
        try:
            return self.configs[ecosystem_id]
        except KeyError:
            cfg = specificConfig(ecosystem_id)
            self.configs[ecosystem_id] = cfg
            return cfg


_manager = Manager()


#---------------------------------------------------------------------------
#   Functions to interact with the module
#---------------------------------------------------------------------------
def getIds(ecosystem: str) -> tuple:
    return _manager.getIds(ecosystem)


class configWatchdog:
    @staticmethod
    def start() -> None:
        globalConfig.start_watchdog()

    @staticmethod
    def stop() -> None:
        globalConfig.stop_watchdog()

    @staticmethod
    def status() -> bool:
        return globalConfig.watchdog


def update() -> None:
    globalConfig.update()


def createEcosystem(*args) -> None:
    if len(args) == 0:
        name = input("Ecosystem name: ")
    else:
        name = args[0]
    globalConfig.create_new_ecosystem(name)


def manageEcosystem() -> None:
    pass


def delEcosystem() -> None:
    pass


def getConfig(ecosystem: str) -> specificConfig:
    return _manager.get_ecosystem_config(ecosystem)
