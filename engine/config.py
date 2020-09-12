#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
import os
import pathlib
import hashlib
import socket
import random
import string
from datetime import date, time, datetime

import ruamel.yaml
import pytz
from tzlocal import get_localzone

logger = logging.getLogger("eng.config")


class Config:
    HEALTH_LOGGING_TIME = "00h00"
    TEST_CONNECTION_IP = "1.1.1.1"
    GAIAWEB_IP = "192.168.1.111"
    GAIAWEB_PORT = 8888
    LIGHT_FREQUENCY = 0.5


class basicConfig(Config):
    """Basic configuration class for GAIA

    This class parses configuration files. It allows to test Internet
    connectivity, returns ecosystems id and names, and create new ecosystems id

    methods available:

    :meth is_connected: Returns True if it is possible to ping the adress given
                        by Config.TEST_CONNECTION_IP.

    :meth ecosystems_id: Returns a list of all the ecosystems id found in the
                         config file

    :meth id_to_name_dict: Returns a dict with the ids as keys and corresponding
                            names as values

    :meth name_to_id_dict: Returns a dict with the names as keys and corresponding
                            ids as values

    :meth status: Returns the status of the ecosystem
    """

    def __init__(self):
        self.yaml = ruamel.yaml.YAML()
        self._custom_cfg_dir = pathlib.Path(__file__).absolute().parents[1]
        try:
            custom_cfg = os.path.join(self._custom_cfg_dir, "ecosystems.cfg")
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
            private_cfg = os.path.join(self._custom_cfg_dir, "private.cfg")
            with open(private_cfg, "r") as file:
                self._private_config = self.yaml.load(file)
        except IOError:
            logger.warning("There is currently no custom private configuration file. "
                           "Using the default settings instead")
            self._private_config = {}
        self.local_timezone = get_localzone()

    @staticmethod
    def str_to_bool(s):
        if s == "True" or "true":
             return True
        elif s == "False" or "false":
             return False
        else:
             raise ValueError(f"{s} can either be 'True'/'true' or 'False'/'false'")

    def update_config_file(self, cfg_type):
        file_name = cfg_type + ".cfg"
        file_path = os.path.join(self._custom_cfg_dir, file_name)
        with open(file_path, "w") as file:
            if cfg_type == "ecosystems":
                self.yaml.dump(self._ecosystems_config, file)
            elif cfg_type == "private":
                raise AttributeError("basicConfig object does not have private "\
                                     "configuration. Use completeConfig instead")

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
        self._ecosystems_config.update(new_ecosystem_cfg)
        self.update_config_file("ecosystems")

    def file_hash(self, cfg_type):
        assert cfg_type in ["ecosystems", "private"]
        file_name = cfg_type + ".cfg"
        file_path = os.path.join(self._custom_cfg_dir, file_name)
        sha = hashlib.sha256()
        BLOCK_SIZE = 4096
        try:
            with open(file_path, 'rb') as f:
                fb = f.read(BLOCK_SIZE)
                while len(fb) > 0:
                    sha.update(fb)
                    fb = f.read(BLOCK_SIZE)
            h = sha.hexdigest()
            return h
        except FileNotFoundError:
            return None
        

    """API calls"""
    @staticmethod
    def is_connected():
      try:
        host = socket.gethostbyname(Config.TEST_CONNECTION_IP)
        s = socket.create_connection((host, 80), 2)
        s.close()
        return True
      except:
         pass
      return False

    @property
    def config_dict(self):
        return self._ecosystems_config

    @property
    def ecosystems_id(self):
        ids = []
        for i in self._ecosystems_config.keys():
            ids.append(i)
        return ids

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
        except:
            return {"latitude": 0, "longitude": 0}

    @home_coordinates.setter
    def set_home_coordinates(self, latitude, longitude):
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
        except:
            return "Somewhere over the rainbow"

    @home_city.setter
    def set_home_city(self, city_name):
        home_city = {"places": {"home": {"city": city_name}}}
        self._private_config.update(home_city)

    def refresh(self):
        pass


class completeConfig(basicConfig):
    def __init__(self, ecosystem_id):
        super().__init__()
        self.ecosystem_id = ecosystem_id

    def update_config_file(self, cfg_type):
        file_name = cfg_type + ".cfg"
        file_path = os.path.join(self._custom_cfg_dir, file_name)
        with open(file_path, "w") as file:
            if cfg_type == "ecosystems":
                self.yaml.dump(self._ecosystems_config, file)
            elif cfg_type == "private":
                self.yaml.dump(self._private_config)

    def refresh_config(self, cfg_type):
        file_name = cfg_type + ".cfg"
        file_path = os.path.join(self._custom_cfg_dir, file_name)
        with open(file_path, "r") as file:
            cfg = self.yaml.load(file)
        if cfg_type == "ecosystems":
            self._ecosystems_config = cfg
        elif cfg_type == "private":
            self._private_config = cfg
        del(cfg)
    
    @property
    def config_dict(self):
        return self._ecosystems_config[self.ecosystem_id]

    @config_dict.setter
    def set_config_dict(self, new_dict):
        self._ecosystems_config[self.ecosystem_id] = new_dict

    @property
    def name(self):
        return self.config_dict["name"]

    @name.setter
    def set_name(self, value):
        self.config_dict["name"] = value
        self.update_config_file("ecosystems")

    @property
    def status(self):
        return self.config_dict["status"]

    @status.setter
    def set_status(self, value):
        self.config_dict["status"] = value

    """Parameters related to sub-processes control"""
    def get_management(self, parameter):
        try:
            return self.config_dict["management"][parameter]
        except:
            return False

    def set_management(self, parameter, value):
        self.config_dict["controls"][parameter] = value

    """Environment related parameters"""
    @property
    def chaos(self):
        try:
            return self.config_dict["environment"]["chaos"]
        except:
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

    """Parameters related to hardware"""    
    @property
    def hardware_dict(self):
        """
        Returns the hardware present in the ecosystem under the form of a dict
        """
        return self.config_dict.get("IO", {})

    def get_hardware_group(self, hardware_type, hardware_level):
        """
        Returns the list of sensors present in the ecosystem
        
        :param hardware_type: str, the type of hardware. Either 'sensor' or 'light'

        :param hardware_level: str, the level at which the hardware operates.
                               Either 'environment' or 'plant'
        """
        if hardware_level not in ("environment", "plant"):
            raise ValueError("'hardware_level' can either be 'environment' or 'plant'")
        sensors = []
        try:
            for hardware in self.hardware_dict.keys():
                if (self.hardware_dict[hardware]["level"] == hardware_level
                    and self.hardware_dict[hardware]["type"] == hardware_type):
                    sensors.append(hardware)
            return sensors
        except: 
            return []

    def get_measure_list(self, hardware_level):
        """
        Returns the set of measures recorded in the ecosystem
        
        :param hardware_level: str, the level at which the hardware operates.
                               Either 'environment' or 'plant'
        """
        if hardware_level not in ("environment", "plant"):
            raise ValueError("hardware_level should be set to either 'environment' or 'plant'")
        measures = []
        for sensor in self.get_hardware_group("sensor", hardware_level):
            data = self.hardware_dict[sensor]["measure"]
            if type(data) == str:
                measures.append(data)
            else:
                for subdata in data:
                    measures.append(subdata)
        return measures

    def get_sensors_for_measure(self, hardware_level, measure):
        sensors = []
        if hardware_level == "environment":
            for sensor in self.get_hardware_group("sensor", hardware_level):
                measures = self.hardware_dict[sensor]["measure"]
                if measure in measures:
                    sensors.append(sensor)
        elif hardware_level == "plant":
            for sensor in self.get_hardware_group("sensor", hardware_level):
                measures = self.hardware_dict[sensor]["plant"]
                if measure in measures:
                    sensors.append(sensor)
        return sensors

    def create_new_hardware_id(self):
        k = 16
        used_ids = list(self.hardware_dict.keys())
        while True:
            x = "".join(random.choices(string.ascii_letters + string.digits, k=k))
            if x not in used_ids:
                break
        return x

    def create_new_hardware(self, name, pin, hardware_type, hardware_level, 
                            model, measure, plant = ""):
        used_pins = []
        for hardware in self.hardware_dict:
            #need to check pins for all! ecosystems
            used_pins.append(self.hardware_dict[harware]["pin"])
        assert pin not in used_pins, f"Pin {pin} already used"
        h_id = self.create_new_hardware_id()
        if hardware_level != "plant":
            new_hardware = {
                h_id: {
                    "name": name,
                    "pin": pin,
                    "type": hardware_type,
                    "level": hardware_level,
                    "model": model,
                    "measure": measure,
                    }
                }
        else:
            assert plant != "", "You need to provide a plant name"
            new_hardware = {
                h_id: {
                    "name": name,
                    "pin": pin,
                    "type": hardware_type,
                    "level": hardware_level,
                    "model": model,
                    "measure": measure,
                    "plant": plant,
                    }
                }
        self.hardware_dict.update(new_hardware)
        self.update_config_file("ecosystems")

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
    def set_time_parameters(self, value):
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
        local_time = local_dt.astimezone(self.local_timezone).time()
        return local_time

    @property
    def moments(self):
        with open("engine/cache/sunrise.cch", "r") as file:
            sunrise = self.yaml.load(file)
        def import_daytime_event(daytime_event):
            try:
                mytime = datetime.strptime(sunrise[daytime_event], "%I:%M:%S %p").time()
                local_time = self.utc_time_to_local_time(mytime)
                return local_time
            except:
                return None
        moments = {}
        moments["twilight_begin"] = import_daytime_event("civil_twilight_begin") or time(8, 00)
        moments["sunrise"] = import_daytime_event("sunrise") or time(8, 00)
        moments["sunset"] = import_daytime_event("sunset") or time(20, 00)
        moments["twilight_end"] = import_daytime_event("civil_twilight_end") or time(20, 00)
        return moments


DEFAULT_ECOSYSTEM_CFG = {
    "O6pH3ei3": {
        "name": "",
        "status": False,
        "management": {
            "lighting": False,
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

if __name__ == "__main__":
    x = basicConfig()
    y = x.status("WK62UprY")
    print(type(y))