# -*- coding: utf-8 -*-

import logging


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

class sensor:
    MEASURE = None
    
    def __init__(self, sensor_id, address, model, name=None, level="environment"):
        self._id = sensor_id
        self._address = address
        self._model = model
        self._name = name or sensor_id
        self._level = level


class DHTSensor(sensor):
    """
    
    """
    def __init__(self, sensor_id, address, model, name=None, level="environment", 
                 extra_measures=[], unit="celcius"):
        super(sensor, self).__init__(sensor_id, address, model, name, level)
        if self._model == "DHT22":
            self._model = dht.DHT22
        elif self._model == "DHT11":
            self._model = dht.DHT11
        self._pin = pin_translation(self._address, "to_BCM")
        self._unit = unit
        self._extra_measures = extra_measures
        self.measures = ["temperature", "humidity"] + self._extra_measures

    def get_data(self):
        data = None
        try:
            data = {}
            data["humidity"], data["temperature"] =\
                dht.read_retry(self._model, self._pin_BCM, 5)
            if "dew_point" in self._extra_measures:
                data["dew_point"] = dew_point(data["temperature"], data["humidity"])
            if "absolute_humidity" in self._extra_measures:
                data["absolute_humidity"] = absolute_humidity(data["temperature"], data["humidity"])
        except Exception as e:
            sensorLogger.error(f"Error message: {e}")
        return data

class DHT22Sensor(DHTSensor):
    def __init__(self, sensor_id, pin, model="DHT22", name=None, level="environment"):
        super(DHTSensor, self).__init__(sensor_id, pin, model, name, level)


