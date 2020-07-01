#!/usr/bin/env python3
# -*- coding: utf-8 -*-
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,

    "formatters": {
        "streamFormat":{
            "format": "%(asctime)s [%(levelname)-4.4s] -- %(name)-18.18s -- %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S"
            },
        "fileFormat": {
            "format": "%(asctime)s -- [%(levelname)-4.4s]  -- %(name)-18.18s -- %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S"
            },
        },

    "handlers": {
        "streamHandler": {
            "level": "INFO",
            "formatter": "streamFormat",
            "class": "logging.StreamHandler",
            },
        "gaiaHandler": {
            "level": "INFO",
            "formatter": "fileFormat",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "gaia.log",
            "mode": "w",
            "maxBytes": 1024*1024*10,
            "backupCount": 5,
            },
        "serverHandler": {
            "level": "INFO",
            "formatter": "fileFormat",
            "class": "logging.handlers.TimedRotatingFileHandler",
            "filename": "server.log",
            "when": "W6",
            "interval": 1,
            "backupCount": 5
            },
        },

    "loggers": {
        "gaia": {
            "handlers": ["streamHandler", "gaiaHandler"],
            "level": "INFO"
            },
        "eng": {
            "handlers": ["streamHandler", "gaiaHandler"],
            "level": "INFO"
            },
        "apscheduler": {
            "handlers": ["streamHandler", "gaiaHandler"],
            "level": "WARNING"
            },
        "enginio.server": {
            "handlers": ["streamHandler", "serverHandler"],
            "level": "WARNING"
            },
        "socket.server": {
            "handlers": ["streamHandler", "serverHandler"],
            "level": "WARNING"
            },
        "geventwebsocket": {
            "handlers": ["streamHandler", "gaiaHandler"],
            "level": "WARNING"
            },
        },
    }

import logging
logger = logging.getLogger("eng.config")

import ruamel.yaml
from datetime import date, time, datetime, timedelta
import pytz
import random
import string
import pathlib
import os
from termcolor import colored

class gaiaConfig():
    """Configuration class for GAIA

    This class parses gaiaEngine configuration files and returns the variables
    required to properly run an engine instance.

    :param ecosystem: Name of the ecosystem to configure. It will internally be
                      translated to ecosystem id.
                      By default, it is set to "None", making gaiaConfig naive. 
                      If you set it to a name that can be found in the config 
                      file, it will become aware. 
                      As long as gaiaConfig is naive, you are only allowed to
                      use the following attributes: "ecosystems_id",
                      "ecosystems_name", "id_to_name_dict" and "dict_to_id_dict".

    attributes available:

    :attr ecosystems_id: Returns a list of all the ecosystems id found in the
                          config file

    :attr ecosystem_name: Returns a list of all the ecosystems name found in the
                          config file

    :attr id_to_name_dict: Returns a dict with the ids as keys and corresponding
                            names as values

    :attr name_to_id_dict: Returns a dict with the names as keys and corresponding
                            ids as values

    :attr status: Returns the status of the ecosystem
    """

    def __init__(self, ecosystem = None):
        #Import yaml parser and current directory path
        self._yaml = ruamel.yaml.YAML()
        self._directory = pathlib.Path(__file__).parent.absolute()
        self._parent_directory = self._directory.parent

        #Try to import custom ecosystem configuration file. If it doesn't exist, use default
        try:
            custom_ecosystem = os.path.join(self._parent_directory, "ecosystems.cfg")
            self._ecosystems_config = self._load_file(custom_ecosystem)
            self.default = False
        except IOError:
            logger.warning("There is currently no custom ecosystem configuration file. "
                           "Using the default settings instead")
            default_ecosystem = os.path.join(self._directory, "default.cfg")
            self._ecosystems_config = self._load_file(default_ecosystem)
            self.defaut = True

        #Try to import custom private configuration file. If it doesn't exist, use default
        try:
            private_file = os.path.join(self._parent_directory, "private.cfg")
            self._private_config = self._load_file(private_file)
        except IOError:
            logger.warning("There is currently no custom private configuration file. "
                           "Using the default settings instead")

        #Translate the ecosystem name given into the corresponding id to easily navigate in the\
        #configuration file
        #Then check if the ecosystem given is in the configuration list
        if ecosystem != None:
            ecosystem_id = self.name_to_id_dict[ecosystem]
            self.ecosystem = ecosystem_id
            assert self.ecosystem in self.ecosystems_id,\
            ("This ecosystem was not found in the configuration file")
        else:
            print(colored("No argument passed to gaiaConfig(), "
                          "only a few functions will be available",
                          "yellow"))

    """Load, update and reset config"""
    def _load_file(self, file_path):
        file = open(file_path, "r")
        file_loaded = self._yaml.load(file)
        file.close()
        return file_loaded

    def _update_private(self, section, value):
        self._private_config[section] = value
        self._save_config(self._private_config)
        pass

    def _save_config(self, config_to_save):
        if config_to_save == self._ecosystems_config:
            file = open("config/ecosystems.cfg", "w")
            self._yaml.dump(config_to_save, file)
            file.close()
        elif config_to_save == self._private_config:
            file = open("config/private.cfg", "w")
            self._yaml.dump(config_to_save, file)
            file.close()
        else:
            print("error")

    def reset_config(self):
        pass

    """Functions not requiring to enter an ecosystem name"""
    @property
    def ecosystems_id(self):
        ids = []
        for i in self._ecosystems_config.keys():
            ids.append(i)
        return ids

    @property
    def ecosystems_name(self):
        names = []
        for i in self._ecosystems_config.keys():
            names.append(self._ecosystems_config[i]["name"])
        return names

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

    def create_new_id(self, ecosystem_or_sensor):
        if ecosystem_or_sensor == "ecosystem":
            k = 8
            used_ids = self.ecosystems_id
        elif ecosystem_or_sensor == "sensor":
            k = 16
            used_ids = self.get_sensor_list(self, "environment")
            used_ids.append(self.get_sensor_list(self, "plant"))
        else:
            raise ValueError("argument should be set to 'ecosystem' or 'sensor'")
        while True:
            x = "".join(random.choices(string.ascii_letters + string.digits, k=k))
            if x not in used_ids:
                break
        return x

    """Functions requiring a valid ecosystem name"""
    #Check if the gaiaConfig instance is naive or not
    #Naive gaiaConfing do not have ecosystem parameter set
    def _check_naiveness(self, fct_name):
        try:
            self._ecosystems_config[self.ecosystem]
        except AttributeError:
            raise AttributeError("naive 'gaiaConfig' object has no attribute '{}'"
                                 .format(fct_name))

    """Ecosystems related parameters"""
    @property
    def status(self):
        self._check_naiveness("status")
        return self._ecosystems_config[self.ecosystem]["status"]

    def set_status(self, new_status):
        self._check_naiveness("set_status")
        if new_status in ["on", "off"]:
            self._ecosystems_config[self.ecosystem]["status"] = new_status
        else:
            raise ValueError("status can be set either to 'on' or 'off'")

    @property
    def type(self):
        self._check_naiveness("type")
        return self._ecosystems_config[self.ecosystem]["environment"]["type"]

    def set_type(self, eco_type):
        self._check_naiveness("set_type")
        if eco_type in ["active", "passive"]:
            self._ecosystems_config[self.ecosystem]["environment"]["type"] = eco_type
        else:
            raise ValueError("Ecosystem type can either be 'active' or 'passive'")

    @property
    def plants(self):
        self._check_naiveness("plants")
        return self._ecosystems_config[self.ecosystem]["plants"]

    def set_plants(self, *args):
        self._check_naiveness("set_plants")
        self._ecosystems_config[self.ecosystem]["plants"] = args

    """Hardware related parameters"""    
    @property
    def hardware_ids(self):
        self._check_naiveness("hardware_ids")
        ids = []
        try:
            for key in self._ecosystems_config[self.ecosystem]["IO"].keys():
                ids.append(key)
            return ids
        except AttributeError:
            return []

    @property
    def hardware_names(self):
        self._check_naiveness("hardware_names")
        names = []
        try:
            for key in self._ecosystems_config[self.ecosystem]["IO"].keys():
                names.append(self._ecosystems_config[self.ecosystem]["IO"][key]["name"])
            return names
        except:
            return []

    @property
    def address_to_name_dict(self):
        self._check_naiveness("address_to_name_dict")
        translator = {}
        for hardware in self.hardware_ids:
            translator[hardware] = self._ecosystems_config[self.ecosystem]["IO"][hardware]["name"]
        return translator

    @property
    def name_to_address_dict(self):
        self._check_naiveness("name_to_address_dict")
        translator = self.address_to_name_dict
        inv_dict = {v: k for k, v in translator.items()}
        return inv_dict

    def get_sensor_list(self, level):
        """
        Returns the list of sensors present in the ecosystem, 'None' if empty
        
        :param level: str, either 'environment' or 'plant'
        """
        self._check_naiveness("get_sensor_list")
        if level not in ["environment", "plant"]:
            raise ValueError("level should be set to either 'environment' or 'plant'")
        sensors = []
        try:
            for hardware in self._ecosystems_config[self.ecosystem]["IO"].keys():
                if (self._ecosystems_config[self.ecosystem]["IO"][hardware]["level"] == level
                    and self._ecosystems_config[self.ecosystem]["IO"][hardware]["type"] == "sensor"):
                    sensors.append(hardware)
            return sensors
        except: #AttributeError
            return []
    
    def get_light_list(self):
        """
        Returns the list of lights present in the ecosystem, 'None' if empty
        """
        self._check_naiveness("get_light_list")
        lights = []
        try:
            for hardware in self._ecosystems_config[self.ecosystem]["IO"].keys():
                if self._ecosystems_config[self.ecosystem]["IO"][hardware]["type"] == "light":
                    lights.append(hardware)
            return lights
        except: #AttributeError
            return []

    def get_hardware_name(self, hardware):
        self._check_naiveness("get_hardware_name")
        try:
            return self._ecosystems_config[self.ecosystem]["IO"][hardware]["name"]
        except:
            raise AttributeError("No hardware called {} found".format(hardware))

    def get_hardware_pin(self, hardware):
        self._check_naiveness("get_hardware_pin")
        try:
            return self._ecosystems_config[self.ecosystem]["IO"][hardware]["pin"]
        except:
            raise AttributeError("No hardware called {} found".format(hardware))

    def get_hardware_model(self, hardware):
        self._check_naiveness("get_hardware_model")
        try:
            return self._ecosystems_config[self.ecosystem]["IO"][hardware]["model"]
        except:
            raise AttributeError("No hardware called {} found".format(hardware))
    @property
    def sensor_dict(self):
        self._check_naiveness("sensor_dict")
        sensor = {}
        try:
            for hardware in self.hardware_ids:
                if self._ecosystems_config[self.ecosystem]["IO"][hardware]["type"] == "sensor":
                    sensor[hardware] = self._ecosystems_config[self.ecosystem]["IO"][hardware]
            return sensor
        except: #No IO section or set to None
            raise AttributeError("No sensor found in the configuration file")

    def get_measure_list(self, level):
        self._check_naiveness("get_measure_list")
        """
        Returns the set of measures recorded in the ecosystem, 'None' if empty
        
        :param level: str, either 'environment' or 'plant'
        """
        if level not in ["environment", "plant"]:
            raise ValueError("level should be set to either 'environment' or 'plant'")
        measures = []
        for sensor in self.get_sensor_list(level):
            data = self._ecosystems_config[self.ecosystem]["IO"][sensor]["measure"]
            if type(data) == str:
                measures.append(data)
            else:
                for subdata in data:
                    measures.append(subdata)
        return measures

    def get_sensors_for_measure(self, level, measure):
        self._check_naiveness("get_sensors_for_measure")
        if level == "environment":
            sensors = []
            for sensor in self.get_sensor_list(level):
                measures = self._ecosystems_config[self.ecosystem]["IO"][sensor]["measure"]
                if measure in measures:
                    sensors.append(sensor)
        elif level == "plant":
            sensors = []
            for sensor in self.get_sensor_list(level):
                measures = self._ecosystems_config[self.ecosystem]["IO"][sensor]["plant"]
                if measure in measures:
                    sensors.append(sensor)
        return sensors

    @property
    def plants_with_sensor(self):
        """
        Returns the unique list of plants having at least one sensor, 'None' if empty
        """
        self._check_naiveness("plants_with_sensors")
        plant_sensors = self.get_sensor_list("plant")
        plants = []
        try:
            for plant_sensor in plant_sensors:
                plants.append(self._ecosystems_config[self.ecosystem]["IO"][plant_sensor]["plant"])
            return plants
        except:
            return []

    """Light related  parameters"""
    def _config_to_time(self, time_formatted):
        hours, minutes = time_formatted.split("h")
        return time(int(hours), int(minutes))

    def _time_to_utc_datetime(self, mytime):
        mydatetime = datetime.combine(date.today(), mytime)
        utc_datetime = pytz.utc.localize(mydatetime)
        return utc_datetime

    def _utc_to_local(self, mydatetime):
        return mydatetime.astimezone(pytz.timezone(self.local_timezone))

    @property
    def light_parameters(self):
        self._check_naiveness("light_parameters")
        try:
            light = {}
            parameters = self._ecosystems_config[self.ecosystem]["environment"]
            day = parameters["day_start"]
            light["day"] = self._config_to_time(day)
            night = parameters["night_start"]
            light["night"] = self._config_to_time(night)
            hours, minutes = parameters["sun_offset"].split("h")
            light["sun_offset"] = timedelta(hours = int(hours), minutes = int(minutes))
            return light
        except:
            raise AttributeError("No light parameter set")

    def set_light_parameters(self, day_start, night_start):
        self._check_naiveness("set_light_parameters")
        self._ecosystems_config[self.ecosystem]["environment"]["day_start"] = day_start
        self._ecosystems_config[self.ecosystem]["environment"]["night_start"] = night_start
        self._save_config(self._ecosystems_config)

    def utc_time_to_local_time(self, mytime):
        mydatetime = datetime.combine(date.today(), mytime)
        mydatetime = pytz.utc.localize(mydatetime)
        local_time = mydatetime.astimezone(pytz.timezone(self.local_timezone)).time()
        return local_time

    @property
    def sun_times(self):
        self._check_naiveness("sun_times")
        def import_daytime_event(daytime_event):
            try:
                sunrise = self._load_file("engine/cache/sunrise.cch")
                mytime = datetime.strptime(sunrise[daytime_event], "%I:%M:%S %p").time()
                local_time = self.utc_time_to_local_time(mytime)
                return local_time
            except TypeError:
                pass
        event = {}
        event["twilight_begin"] = import_daytime_event("civil_twilight_begin")
        event["sunrise"] = import_daytime_event("sunrise")
        event["sunset"] = import_daytime_event("sunset")
        event["twilight_end"] = import_daytime_event("civil_twilight_end")
        return event

    '''Environment related parameters'''
    @property
    def climate_type(self):
        self._check_naiveness("climate_type")
        return self._ecosystems_config[self.ecosystem]["environment"]["control"]         

    def check_chaos(self):
        test = self._ecosystems_config[self.ecosystem]["environment"]["chaos"]
        if test != 0:
            return True
        else:
            return False

    @property
    def chaos_factor(self):
        self._check_naiveness("chaos_factor")
        try:
            return self._ecosystems_config[self.ecosystem]["environment"]["chaos"]
        except:
            raise AttributeError("Chaos was not configure for {}".format(self.ecosystem))

    '''
    def set_climate_type(self, new_climate_type):
        self._check_naiveness("set_climate_type")

        climate_type = self.climate_type
        if new_climate_type in ["day&night", "continuous"]:
            self._ecosystems_config[self.ecosystem]["environment"]["control"] = new_climate_type
            if climate_type == "continuous":
                self._ecosystems_config[self.ecosystem]["environment"]["day"] = self._ecosystems_config[self.ecosystem]["environment"]["continuous"]
                self._ecosystems_config[self.ecosystem]["environment"]["night"] = self._ecosystems_config[self.ecosystem]["environment"]["continuous"]
                del(self._ecosystems_config[self.ecosystem]["environment"]["continuous"])

            else:
                self._ecosystems_config[self.ecosystem]["environment"]["continuous"] = self._ecosystems_config[self.ecosystem]["environment"]["day"]
                del(self._ecosystems_config[self.ecosystem]["environment"]["day"])
            self._save_config(self._ecosystems_config)
        else:
            return "Climate type can either be 'day&night' or 'continuous'"

    def get_climate_parameters(self, temp_or_hum):
        self._check_naiveness("get_climate_parameters")
        dict = {}
        climate_type = self.climate_type
        ecosystem_type = self.ecosystem_type
        if temp_or_hum in ["temperature", "humidity"]:
            if climate_type == "continuous":
                if ecosystem_type == "active":
                    dict = self._ecosystems_config[self.ecosystem]["environment"]["continuous"][temp_or_hum]
                else: #if ecosystem_type == "passive
                    for min_or_max in ["min", "max"]:
                        dict[min_or_max] = self._ecosystems_config[self.ecosystem]["environment"]["continuous"][temp_or_hum][min_or_max]
            else: #if climate_type == "day&night"
                if ecosystem_type == "active":
                    for day_or_night in ["day", "night"]:
                        dict[day_or_night] = self._ecosystems_config[self.ecosystem]["environment"][day_or_night][temp_or_hum]
                else: #if ecosystem_type == "passive
                    for day_or_night in ["day", "night"]:
                        dict[day_or_night] = {}
                        for min_or_max in ["min", "max"]:
                            dict[day_or_night][min_or_max] = self._ecosystems_config[self.ecosystem]["environment"][day_or_night][temp_or_hum][min_or_max]
            return dict
        else:
            return "Type of climate parameter can either be 'temperature' or 'humidity'"

    def set_climate_parameters(self, temp_or_hum, value):
        self._check_naiveness("set_climate_parameters")
        climate_type = self.get_climate_type()
        if climate_type == "continuous":
            self._ecosystems_config[self.ecosystem]["environment"]["continuous"][temp_or_hum] = value
        else: #if climate_type == "day&night"
            for day_or_night in ["day", "night"]:
                    self._ecosystems_config[self.ecosystem]["environment"][day_or_night][temp_or_hum] = value[day_or_night]
    '''

    """Private config parameters"""
    @property
    def home_coordinates(self):
        try:
            if "home" in self._private_config["places"]:
                return self._private_config["places"]["home"]["coordinates"]
            else:
                return {"latitude": 0, "longitude": 0}
        except AttributeError:
            return {"latitude": 0, "longitude": 0}

    def set_home_coordinates(self, latitude, longitude):
        mydict = {"latitude": latitude, "longitude": longitude}
        pass

    @property
    def home_city(self):
        try:
            if "home" in self._private_config["places"]:
                return self._private_config["places"]["home"]["city"]
            else:
                return "Somewhere over the rainbow"
        except AttributeError:
            return "Somewhere over the rainbow"

    def set_home_city(self):
        pass

    @property
    def local_timezone(self):
        try:
            if "timezone" in self._private_config:
                return str(self._private_config["timezone"])
            else:
                return "UTC"
        except AttributeError:
            return "UTC"

    def set_local_timezone(self, timezone):
        self._update_private("timezone", timezone)

#keep for maybe later
    @property
    def database_info(self):
        return self._private_config["database"] if "database" in self._private_config else None

    def set_database_info(self, host, db, user, passwd):
        mydict = {"host": host, "db": db, "user": user, "passwd": passwd}
        self._update_private("database", mydict)

x = gaiaConfig("B612")