import logging
import random

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

    def get_data(self):
        return {}


class DHTSensor(baseSensor):
    def __init__(self, hardware_id, address, model, name=None, level="environment",
                 max_diff=None, unit="celsius"):
        super(DHTSensor, self).__init__(hardware_id, address, model, name, level, max_diff)
        self._pin = pin_translation(self._address, "to_BCM")
        self._unit = unit
        self._extra_measures = []
        self.measures = ["temperature", "humidity"]
        self.update_measures()
        self._last_data = {}

    def update_measures(self):
        self.measures = ["temperature", "humidity"] + self._extra_measures

    def set_extra_measures(self, extra_measures=[]):
        self._extra_measures = extra_measures

    def get_data(self):
        data = {}
        try:
            for retry in range(3):
                data["humidity"], data["temperature"] = \
                    dht.read_retry(self._model, self._pin, 5)
                if not (abs(self._last_data.get("humidity", data["humidity"]) -
                            data["humidity"]) > 7.5 or
                        abs(self._last_data.get("temperature", data["temperature"]) -
                            data["temperature"]) > 2):
                    break
            if "dew_point" in self._extra_measures:
                data["dew_point"] = dew_point(data["temperature"], data["humidity"])
            if "absolute_humidity" in self._extra_measures:
                data["absolute_humidity"] = absolute_humidity(data["temperature"], data["humidity"])
        except Exception as e:
            sensorLogger.error(f"Error message: {e}")
        self._last_data = data
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


DEBUG_SENSORS = {sensor.MODEL: sensor for sensor in
                 [debugSensor_Mega,
                  debugSensor_Moisture]}

GPIO_SENSORS = {sensor.MODEL: sensor for sensor in
                [DHT22Sensor]}

SENSORS_AVAILABLE = {**DEBUG_SENSORS,
                     **GPIO_SENSORS}

HARDWARE_AVAILABLE = SENSORS_AVAILABLE
