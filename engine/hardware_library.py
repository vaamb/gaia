# -*- coding: utf-8 -*-
"""
Do i2c config

Ex with VEML7700:
i2c = busio.I2C(board.SCL, board.SDA)
VEML7700 = adafruit_veml7700.VEML7700(i2c)

data = VEML7700.lux
"""


import logging
import random

#import board
#import busio
#import adafruit_veml7700

try:
    import RPi.GPIO as GPIO
except:
    from stupid_PI import GPIO

try:
    import Adafruit_DHT as dht
except:
    from stupid_PI import dht

from .utils import dew_point, absolute_humidity, temperature_converter, pin_translation


sensorLogger = logging.getLogger("Truc")


class hardware:    
    def __init__(self, hardware_id, address, model, name=None, level="environment"):
        self._uid = hardware_id
        self._address = address
        self._model = model
        self._name = name or hardware_id
        self._level = level

    @property
    def uid(self):
        return self._uid

    @property
    def level(self):
        return self._level

class baseSensor(hardware):
    def __init__(self, hardware_id, address, model, name=None, level="environment", 
                 max_diff=None):
        super(baseSensor, self).__init__(hardware_id, address, model, name, level)
        self._max_diff = max_diff

    def get_data():
        return {}

class DHTSensor(baseSensor):
    def __init__(self, hardware_id, address, model, name=None, level="environment",
                 max_diff=None, unit="celcius"):
        super(DHTSensor, self).__init__(hardware_id, address, model, name, level, max_diff)
        self._pin = pin_translation(self._address, "to_BCM")
        self._unit = unit
        self._extra_measures = []
        self.update_measures()

    def update_measures(self):
        self.measures = ["temperature", "humidity"] + self._extra_measures

    def set_extra_measures(self, extra_measures=[]):
        self._extra_measures = extra_measures
        
    def get_data(self):
        data = {}
        try:
            data["humidity"], data["temperature"] =\
                dht.read_retry(self._model, self._pin, 5)
            if "dew_point" in self._extra_measures:
                data["dew_point"] = dew_point(data["temperature"], data["humidity"])
            if "absolute_humidity" in self._extra_measures:
                data["absolute_humidity"] = absolute_humidity(data["temperature"], data["humidity"])
        except Exception as e:
            sensorLogger.error(f"Error message: {e}")
        return data

class DHT22Sensor(DHTSensor):
    MODEL = "DHT22"
    def __init__(self, hardware_id, address, model=dht.DHT22, name=None, level="environment",
                 max_diff=None):
        super(DHT22Sensor, self).__init__(hardware_id, address, model, name, level, max_diff)

class debugSensor_Mega(baseSensor):
    MODEL = "debugMega"
    
    def get_data(self):
        data = {}
        try:
            temperature_data = random.uniform(17, 30)
            humidity_data = random.uniform(20, 55)
            light_data = random.randrange(1000, 100000, 10)
            data["humidity"] = round(humidity_data, 1)
            data["temperature"] = round(temperature_data, 1)
            data["light"] = light_data
        except Exception as e:
            sensorLogger.error(f"Error message: {e}")        
        return data

class debugSensor_Moisture(baseSensor):
    MODEL = "debugMoisture"
    
    def get_data(self):
        data = {}
        try:
            moisture_data = random.uniform(10, 55)
            data["moisture"] = round(moisture_data, 1)
        except Exception as e:
            sensorLogger.error(f"Error message: {e}")        
        return data

DEBUG_SENSORS = [debugSensor_Mega, debugSensor_Moisture]
SENSORS_AVAILABLE = [DHT22Sensor] + DEBUG_SENSORS
HARDWARE_AVAILABLE = SENSORS_AVAILABLE