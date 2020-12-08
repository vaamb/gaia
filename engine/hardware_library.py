import logging
import random

"""VEML
import time
import board
import busio
import adafruit_veml7700
"""

try:
    import RPi.GPIO as GPIO
except ImportError:
    from stupid_PI import GPIO

try:
    import Adafruit_DHT as dht
except ImportError:
    from stupid_PI import dht

from .utils import dew_point, absolute_humidity, temperature_converter, pin_translation


sensorLogger = logging.getLogger("eng.hardware_lib")


class hardware:
    def __init__(self, **kwargs):
        self._uid = kwargs.pop("hardware_id")
        self._address = kwargs.pop("address")
        self._model = kwargs.pop("model", None)
        self._name = kwargs.pop("name", self._uid)
        self._level = kwargs.pop("level", "environment")

    @property
    def uid(self) -> str:
        return self._uid

    @property
    def address(self) -> str:
        return self._address

    @property
    def model(self) -> str:
        return self._model

    @property
    def name(self) -> str:
        return self._name

    @property
    def level(self) -> str:
        return self._level


class baseSensor(hardware):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._max_diff = kwargs.pop("max_diff", None)
        self._measure = kwargs.pop("measure", [])

    def get_data(self) -> dict:
        return {}

    @property
    def measure(self) -> list:
        return self._measure

    @measure.setter
    def measure(self, measure: list) -> None:
        self._measure = measure


class DHTSensor(baseSensor):
    def __init__(self, **kwargs):
        if not kwargs.get("measure", []):
            kwargs["measure"] = ["temperature", "humidity"]
        super().__init__(**kwargs)
        self._pin = pin_translation(self._address, "to_BCM")
        self._unit = kwargs.pop("unit", "celsius")
        self._last_data = {}

    def get_data(self) -> dict:
        data = {}
        try:
            for retry in range(3):
                data["humidity"], data["temperature"] = \
                    dht.read_retry(self._model, self._pin, 5)
                # Check if sudden change in humidity or temperature as it is
                # an often observed DHT bug
                if not (abs(self._last_data.get("humidity", data["humidity"]) -
                            data["humidity"]) > 15 or
                        abs(self._last_data.get("temperature", data["temperature"]) -
                            data["temperature"]) > 5):
                    break
            if "dew_point" in self._measure:
                data["dew_point"] = dew_point(data["temperature"], data["humidity"])
            if "absolute_humidity" in self._measure:
                data["absolute_humidity"] = absolute_humidity(data["temperature"], data["humidity"])
            if "humidity" not in self._measure:
                del data["humidity"]
            if "temperature" not in self._measure:
                del data["temperature"]
        except Exception as e:
            sensorLogger.error(f"Sensor {self._name} encountered an error. "
                               f"Error message: {e}")
            data = {}
        self._last_data = data
        return data


class DHT22Sensor(DHTSensor):
    MODEL = "DHT22"

    def __init__(self, **kwargs):
        kwargs["model"] = dht.DHT22
        super().__init__(**kwargs)


class VEML7700(baseSensor):
    MODEL = "_VEML7700"


class debugSensor_Mega(baseSensor):
    MODEL = "debugMega"

    def get_data(self) -> dict:
        data = {}
        try:
            temperature_data = random.uniform(17, 30)
            humidity_data = random.uniform(20, 55)
            light_data = random.randrange(1000, 100000, 10)
            data["humidity"] = round(humidity_data, 1)
            data["temperature"] = round(temperature_data, 1)
            data["light"] = light_data
        except Exception as e:
            sensorLogger.error(f"Sensor {self._name} encountered an error. "
                               f"Error message: {e}")
        return data


class debugSensor_Moisture(baseSensor):
    MODEL = "debugMoisture"

    def get_data(self) -> dict:
        data = {}
        try:
            moisture_data = random.uniform(10, 55)
            data["moisture"] = round(moisture_data, 1)
        except Exception as e:
            sensorLogger.error(f"Sensor {self._name} encountered an error. "
                               f"Error message: {e}")
        return data


DEBUG_SENSORS = {sensor.MODEL: sensor for sensor in
                 [debugSensor_Mega,
                  debugSensor_Moisture]}

GPIO_SENSORS = {sensor.MODEL: sensor for sensor in
                [DHT22Sensor]}

SENSORS_AVAILABLE = {**DEBUG_SENSORS,
                     **GPIO_SENSORS}

HARDWARE_AVAILABLE = SENSORS_AVAILABLE
