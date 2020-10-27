#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
import pathlib
import hashlib
import socket
import random
import string
from datetime import date, time, datetime
from threading import Thread, Event

import ruamel.yaml
import pytz
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

def str_to_bool(s):
    if s == "True" or "true":
         return True
    elif s == "False" or "false":
         return False
    else:
         raise ValueError(f"{s} can either be 'True'/'true' or 'False'/'false'")

#---------------------------------------------------------------------------
#   basicConfig class
#---------------------------------------------------------------------------
class _basicConfig():
    def __init__(self):
        self.yaml = ruamel.yaml.YAML()
        self._load_config()
        self.watchdog = False
        self._internal_change = False

    def _load_config(self):
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
            #create a new ecosystem, which is loaded as self._ecosystems_config
            self._ecosystems_config = {}
            self.create_new_ecosystem("Default Ecosystem")
            self.default = True

        #Try to import custom private configuration file. If it doesn't exist, use default
        try:
            private_cfg = gaiaEngine_dir/"private.cfg"
            with open(private_cfg, "r") as file:
                self._private_config = self.yaml.load(file)
        except IOError:
            logger.warning("There is currently no custom private configuration file. "
                           "Using the default settings instead")
            self._private_config = {}

    def update(self):
        logger.debug("Updating configuration")
        self._load_config()

    def save(self, cfg):
        file_path = gaiaEngine_dir/f"{cfg}.cfg"
        with open(file_path, "w") as file:
            if cfg == "ecosystems":
                self.yaml.dump(self._ecosystems_config, file)
            elif cfg == "private":
                self.yaml.dump(self._private_config)

    def create_new_ecosystem_id(self):
        k = 8
        used_ids = self.ecosystems_id
        while True:
            x = "".join(random.choices(string.ascii_letters + string.digits, k=k))
            if x not in used_ids:
                break
        return x

    def create_new_ecosystem(self, ecosystem_name):
        new_ecosystem_cfg = DEFAULT_ECOSYSTEM_CFG
        new_id = self.create_new_ecosystem_id()
        old_id = list(new_ecosystem_cfg.keys())[0]
        new_ecosystem_cfg[new_id] = new_ecosystem_cfg.pop(old_id)
        new_ecosystem_cfg[new_id]["name"] = ecosystem_name
        """
        self._ecosystems_config.update(new_ecosystem_cfg)
        self.save("ecosystems")
        """

    def file_hash(self, file_path):
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

    def _watchdog(self):
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
    def start_watchdog(self):
        if not self.watchdog:
            self._watchdog_stopEvent = Event()
            self._watchdogThread = Thread(target=self._watchdog, args=())
            self._watchdogThread.name = "configWatchdog"
            self._watchdogThread.start()
            self.watchdog = True
        else:
            logger.debug("Config watchdog is already running")

    def stop_watchdog(self):
        if self.watchdog:
            self._watchdog_stopEvent.set()
            self._watchdogThread.join()
            del self._watchdogThread, self._watchdog_stopEvent
            self.watchdog = False

    @property
    def config_dict(self):
        return self._ecosystems_config

    @config_dict.setter
    def config_dict(self, dct):
        self._ecosystems_config = dct

    @property
    def ecosystems_id(self):
        ids = []
        for i in self._ecosystems_config.keys():
            ids.append(i)
        return ids

    @property
    def ecosystems_name(self):
        names = []
        for id in self.ecosystems_id:
            names.append(self._ecosystems_config[id]["name"])
        return names

    def status(self, ecosystem_id):
        return self._ecosystems_config[ecosystem_id]["status"]

    def set_status(self, ecosystem_id, value):
        self._ecosystems_config[ecosystem_id]["status"] = value

    @property
    def id_to_name_dict(self):
        translator = {}
        for ecosystem in self.ecosystems_id:
            translator[ecosystem] = self._ecosystems_config[ecosystem]["name"]
        return translator

    @property
    def name_to_id_dict(self):
        translator = self.id_to_name_dict
        inv_dict = {v: k for k, v in translator.items()}
        return inv_dict

    """Private config parameters"""
    @property
    def home_coordinates(self):
        try:
            if "home" in self._private_config["places"]:
                return self._private_config["places"]["home"]["coordinates"]
            else:
                return {"latitude": 0, "longitude": 0}
        except Exception as ex:
            print(ex)
        return {"latitude": 0, "longitude": 0}

    @home_coordinates.setter
    def home_coordinates(self, latitude, longitude):
        mydict = {"latitude": latitude, "longitude": longitude}
        coordinates = {"places": {"home": {"coordinates": mydict}}}
        self._private_config.update(coordinates)

    @property
    def home_city(self):
        try:
            if "home" in self._private_config["places"]:
                return self._private_config["places"]["home"]["city"]
            else:
                return "Somewhere over the rainbow"
        except Exception as ex:
            print(ex)
        return "Somewhere over the rainbow"

    @home_city.setter
    def home_city(self, city_name):
        home_city = {"places": {"home": {"city": city_name}}}
        self._private_config.update(home_city)


#---------------------------------------------------------------------------
#   specificConfig class
#---------------------------------------------------------------------------
class specificConfig():
    def __init__(self, ecosystem):
        if ecosystem in globalConfig.ecosystems_id:
            self.ecosystem_id = ecosystem
        elif ecosystem in globalConfig.ecosystems_name:
            self.ecosystem_id = globalConfig.name_to_id_dict[ecosystem]
        else:
            raise ValueError("Please provide either a valid ecosystem id or "
                             "a valid ecosystem name.")
    #config_dict is passed after globalConfig is instanciated
    #specificConfig.config_dict = globalConfig.config_dict
    @property
    def name(self):
        return self.config_dict["name"]

    @property
    def uid(self):
        return self.ecosystem_id

    @name.setter
    def set_name(self, value):
        self.config_dict["name"] = value
        globalConfig.save("ecosystems")

    @property
    def status(self):
        return self.config_dict["status"]

    @status.setter
    def status(self, value):
        self.config_dict["status"] = value

    """Parameters related to sub-processes control"""
    def get_management(self, parameter):
        try:
            return self.config_dict["management"][parameter]
        except Exception as ex:
            print(ex)
            return False

    def set_management(self, parameter, value):
        self.config_dict["controls"][parameter] = value

    """Environment related parameters"""
    @property
    def light_method(self):
        if not is_connected():
            logger.warning("Not connected to the internet, light method automatically turned to 'fixed'")
            return "fixed"
        else:
            return self.config_dict["environment"]["light"]

    @property
    def chaos(self):
        try:
            return self.config_dict["environment"]["chaos"]
        except Exception:
            raise AttributeError("Chaos was not configure for {}".format(self.ecosystem))

    def get_climate_parameters(self, parameter):
        if parameter not in ("temperature", "humidity"):
            raise ValueError("parameter should be set to either 'temperature' or 'humidity'")
        data = {}
        data["hysteresis"] = self.config_dict["environment"]["hysteresis"]
        for moment_of_day in ("day", "night"):
            data[moment_of_day] = self.config_dict["environment"][moment_of_day]["target"]
        return data

    def set_climate_parameters(self, parameter, value):
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
    def IO_dict(self):
        """
        Returns the IO present in the ecosystem under the form of a dict
        """
        return self.config_dict.get("IO", {})

    def get_lights(self):
        lights = []
        try:
            for IO in self.IO_dict.keys():
                if self.IO_dict[IO]["type"] == "light":
                    lights.append(IO)
        except Exception as ex:
            print(ex)
        return lights

    def get_sensors(self):
        sensors = []
        try:
            for IO in self.IO_dict.keys():
                if self.IO_dict[IO]["type"] == "sensor":
                    sensors.append(IO)
        except Exception as ex:
            print(ex)
        return sensors
    
    def get_IO_group(self, _type, level):
        group = []
        for IO in self.IO_dict:
            if self.IO_dict[IO]["type"] == _type and self.IO_dict[IO]["level"] == level:
                group.append(IO)
        return group


    def create_new_IO_id(self):
        k = 16
        used_ids = list(self.IO_dict.keys())
        while True:
            x = "".join(random.choices(string.ascii_letters + string.digits, k=k))
            if x not in used_ids:
                break
        return x

    def create_new_IO(self, name, pin, IO_type, IO_level, 
                            model, measure, plant = ""):
        used_pins = []
        for id in self.config_dict:
            for io in self.config_dict[id]["IO"]:
                #need to check pins for all! ecosystems
                pin = self.config_dict[id]["IO"][io].get("pin", None)
                if pin:
                    used_pins.append(pin)
        assert pin not in used_pins, f"Pin {pin} already used"
        uid = self.create_new_IO_id()
        if IO_level != "plant":
            new_IO = {
                uid: {
                    "name": name,
                    "pin": pin,
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
                    "pin": pin,
                    "type": IO_type,
                    "level": IO_level,
                    "model": model,
                    "measure": measure,
                    "plant": plant
                    }
                }
        self.IO_dict.update(new_IO)
        self.save("ecosystems")

    """Parameters related to time"""
    def human_time_parser(self, human_time):
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
    def time_parameters(self):
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
    def time_parameters(self, value):
        if not isinstance(value, dict):
            raise ValueError("value should be a dict with keys equal to 'day' \
                             or 'night' and values equal to strinf representing \
                             a human readable time, such as '20h00'")
        self.config_dict["environment"]["day"]["start"] =\
            value["day"]["start"]
        self.config_dict["environment"]["night"]["start"] =\
            value["night"]["start"]
        self._save_config(self._ecosystems_config)

    def utc_time_to_local_time(self, utc_time):
        dt = datetime.combine(date.today(), utc_time)
        local_dt = pytz.utc.localize(dt)
        local_time = local_dt.astimezone(localTZ).time()
        return local_time

    @property
    def moments(self):
        with open(gaiaEngine_dir/"cache/sunrise.cch", "r") as file:
            sunrise = globalConfig.yaml.load(file)
        def import_daytime_event(daytime_event):
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
    def __init__(self):
        self.configs = {}

    def getIds(self, ecosystem):
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

    def get_ecosystem_config(self, ecosystem):
        ecosystem_id, ecosystem_name = self.getIds(ecosystem)     
        if ecosystem_id in self.configs:
            cfg = self.configs[ecosystem_id]
        else:
            cfg = specificConfig(ecosystem_id)
            cfg.config_dict = globalConfig.config_dict[ecosystem_id]
            self.configs[ecosystem_id] = cfg
        return cfg


DEFAULT_ECOSYSTEM_CFG = {
    "O6pH3ei3": {
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

globalConfig = _basicConfig()

_manager = Manager()


#---------------------------------------------------------------------------
#   Functions to interact with the module
#---------------------------------------------------------------------------
def getIds(ecosystem):
    return _manager.getIds(ecosystem)

class configWatchdog:
    @staticmethod
    def start():
        globalConfig.start_watchdog()

    @staticmethod
    def stop():
        globalConfig.stop_watchdog()

    @staticmethod
    def status():
        return globalConfig.watchdog

def update():
    globalConfig.update()

def createEcosystem(*args):
    if len(args) == 0:
        name = input("Ecosystem name: ")
    else:
        name = args[0]
    globalConfig.create_new_ecosystem(name)

def manageEcosystem():
    pass

def delEcosystem():
    pass

def getConfig(ecosystem):
    return _manager.get_ecosystem_config(ecosystem)