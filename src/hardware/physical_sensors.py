from time import sleep

from .base import _RASPI, get_i2c, gpioSensor, i2cSensor, sensorLogger
from src.utils import get_absolute_humidity, get_dew_point, \
    temperature_converter

if _RASPI:
    from adafruit_veml7700 import VEML7700 as _VEML7700  # adafruit-circuitpython-veml7700
    from adafruit_dht import DHT11 as _DHT11, DHT22 as _DHT22  # adafruit-circuitpython-dht + sudo apt-get install libgpiod2
else:
    from .compatibility import VEML7700 as _VEML7700
    from .compatibility import DHT11 as _DHT11, DHT22 as _DHT22


# ---------------------------------------------------------------------------
#   GPIO sensors
# ---------------------------------------------------------------------------
class DHTSensor(gpioSensor):
    def __init__(self, **kwargs) -> None:
        if not kwargs.get("measure", []):
            kwargs["measure"] = ["temperature", "humidity"]
        super().__init__(**kwargs)

        self._unit = kwargs.pop("unit", "celsius")

        # Load dht device.
        # Rem: don't use pulseio as it uses 100% of one core in Pi3
        # In Pi0: behaves correctly
        if self._model.upper() == "DHT11":
            self._device = _DHT11(self._pin, use_pulseio=False)
        elif self._model.upper() == "DHT22":
            self._device = _DHT22(self._pin, use_pulseio=False)
        else:
            raise Exception("Unknown DHT model")

        self._raw_data = {}

    def _get_raw_data(self) -> tuple:
        for retry in range(5):
            try:
                self._device.measure()
                humidity = round(self._device.humidity, 2)
                temperature = round(self._device.temperature, 2)

            except RuntimeError:
                sleep(2)
                continue

            except Exception as e:
                sensorLogger.error(
                    f"Sensor {self._name} encountered an error. "
                    f"ERROR msg: {e}")
                break
            return humidity, temperature

    def get_data(self) -> list:
        raw_humidity, raw_temperature = self._get_raw_data()
        data = []
        if raw_humidity is not None and raw_temperature is not None:
            if "humidity" in self._measure:
                data.append({"name": "humidity", "values": raw_humidity})

            if "temperature" in self._measure:
                temperature = temperature_converter(
                                 raw_temperature, "celsius", self._unit)
                data.append({"name": "temperature", "values": temperature})

            if "dew_point" in self._measure:
                raw_dew_point = get_dew_point(raw_temperature, raw_humidity)
                dew_point = temperature_converter(
                    raw_dew_point, "celsius", self._unit)
                data.append({"name": "dew_point", "values": dew_point})

            if "absolute_humidity" in self._measure:
                raw_absolute_humidity = get_absolute_humidity(
                    raw_temperature, raw_humidity)
                data.append({"name": "absolute_humidity", "values": raw_absolute_humidity})
        return data


class DHT11(DHTSensor):
    MODEL = "DHT11"

    def __init__(self, **kwargs) -> None:
        kwargs["model"] = self.MODEL
        super().__init__(**kwargs)


class DHT22(DHTSensor):
    MODEL = "DHT22"

    def __init__(self, **kwargs) -> None:
        kwargs["model"] = self.MODEL
        super().__init__(**kwargs)


GPIO_SENSORS = {hardware.MODEL: hardware for hardware in
                [DHT11,
                 DHT22]}


# ---------------------------------------------------------------------------
#   I2C sensors
# ---------------------------------------------------------------------------
class VEML7700(i2cSensor):
    MODEL = "VEML7700"

    def __init__(self, **kwargs) -> None:
        kwargs["model"] = self.MODEL
        super().__init__(**kwargs)

        if not self._hex_address:
            self._hex_address = 0x10
        self._device = _VEML7700(get_i2c(), self._hex_address)

    def get_data(self) -> list:
        data = []
        try:
            data.append({"name": "light", "values": self._device.lux})
        except Exception as e:
            sensorLogger.error(
                f"Sensor {self._name} encountered an error. "
                f"Error message: {e}")
        return data


I2C_SENSORS = {hardware.MODEL: hardware for hardware in
               [VEML7700]}
