from time import sleep

from . import _RASPI
from .ABC import (
    get_i2c, gpioSensor, i2cSensor, PlantLevelHardware, sensorLogger
)
from ..utils import (
    get_absolute_humidity, get_dew_point, temperature_converter
)


# ---------------------------------------------------------------------------
#   GPIO sensors
# ---------------------------------------------------------------------------
class DHTSensor(gpioSensor):
    def __init__(self, *args, **kwargs) -> None:
        if not kwargs.get("measure", ()):
            kwargs["measure"] = ["temperature", "humidity"]
        super().__init__(*args, **kwargs)

        self._unit = kwargs.pop("unit", "celsius")

        # Load dht device.
        # Rem: don't use pulseio as it uses 100% of one core in Pi3
        # In Pi0: behaves correctly
        self._device = self._get_device()
        self._raw_data = {}

    def _get_device(self):
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    def _get_raw_data(self) -> tuple:
        for retry in range(3):
            try:
                self._device.measure()
                humidity = round(self._device.humidity, 2)
                temperature = round(self._device.temperature, 2)

            except RuntimeError:
                sleep(0.5)
                continue

            except Exception as e:
                sensorLogger.error(
                    f"Sensor {self._name} encountered an error. "
                    f"ERROR msg: `{e.__class__.__name__}: {e}`"
                )
                break

            else:
                return humidity, temperature

    def get_data(self) -> list:
        raw_humidity, raw_temperature = self._get_raw_data()
        data = []
        if raw_humidity is not None and raw_temperature is not None:
            if "humidity" in self.measure:
                data.append({"name": "humidity", "value": raw_humidity})

            if "temperature" in self.measure:
                temperature = temperature_converter(
                                 raw_temperature, "celsius", self._unit
                )
                data.append({"name": "temperature", "value": temperature})

            if "dew_point" in self.measure:
                raw_dew_point = get_dew_point(raw_temperature, raw_humidity)
                dew_point = temperature_converter(
                    raw_dew_point, "celsius", self._unit
                )
                data.append({"name": "dew_point", "value": dew_point})

            if "absolute_humidity" in self.measure:
                raw_absolute_humidity = get_absolute_humidity(
                    raw_temperature, raw_humidity)
                data.append({"name": "absolute_humidity", "value": raw_absolute_humidity})
        return data


class DHT11(DHTSensor):
    def _get_device(self):
        if _RASPI:
            try:
                from adafruit_dht import DHT11 as _DHT11
            except ImportError:
                raise RuntimeError(
                    "Adafruit dht package and libgpiod2 are required. Run "
                    "`pip install adafruit-circuitpython-seesaw` in your "
                    "virtual env and `sudo apt install libgpiod2`."
                )
        else:
            from ._compatibility import DHT11 as _DHT11
        return _DHT11(self._pin, use_pulseio=False)


class DHT22(DHTSensor):
    def _get_device(self):
        if _RASPI:
            try:
                from adafruit_dht import DHT22 as _DHT22
            except ImportError:
                raise RuntimeError(
                    "Adafruit dht package and libgpiod2 are required. Run "
                    "`pip install adafruit-circuitpython-seesaw` in your "
                    "virtual env and `sudo apt install libgpiod2`."
                )
        else:
            from ._compatibility import DHT11 as _DHT22
        return _DHT22(self._pin, use_pulseio=False)


GPIO_SENSORS = {
    hardware.__name__: hardware for hardware in [
        DHT11,
        DHT22,
    ]
}


# ---------------------------------------------------------------------------
#   I2C sensors
# ---------------------------------------------------------------------------
class VEML7700(i2cSensor):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not kwargs.get("measure", ()):
            kwargs["measure"] = ["lux"]
        if not self._address["main"].number:
            self._address["main"].number = 0x10
        self._device = self._get_device()

    def _get_device(self):
        if _RASPI:
            try:
                from adafruit_veml7700 import VEML7700 as _VEML7700
            except ImportError:
                raise RuntimeError(
                    "Adafruit veml7700 package is required. Run `pip install "
                    "adafruit-circuitpython-veml7700` in your virtual env."
                )
        else:
            from ._compatibility import VEML7700 as _VEML7700
        return _VEML7700(get_i2c(), self._address["main"].number)

    # To catch data fast from light routine
    def _get_lux(self) -> float:
        try:
            return self._device.lux
        except Exception as e:
            sensorLogger.error(
                f"Sensor {self._name} encountered an error. "
                f"ERROR msg: `{e.__class__.__name__}: {e}`"
            )

    def get_data(self) -> list:
        data = []
        if "lux" in self.measure or "light" in self.measure:
            data.append({"name": "light", "value": self._get_lux()})
        return data


class CapacitiveSensor(i2cSensor):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not kwargs.get("measure", ()):
            kwargs["measure"] = ["capacitive"]
        if not self._address["main"].number:
            self._address["main"].number = 0x36
        self._unit = kwargs.pop("unit", "celsius")
        self._device = self._get_device()

    def _get_device(self):
        if _RASPI:
            try:
                from adafruit_seesaw.seesaw import Seesaw
            except ImportError:
                raise RuntimeError(
                    "Adafruit seesaw package is required. Run `pip install "
                    "adafruit-circuitpython-seesaw` in your virtual ."
                )
        else:
            from ._compatibility import Seesaw
        return Seesaw(get_i2c(), self._address["main"].number)

    def get_data(self) -> list[dict]:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )


class CapacitiveMoisture(CapacitiveSensor, PlantLevelHardware):
    def __init__(self, *args, **kwargs) -> None:
        if not kwargs.get("measure", ()):
            kwargs["measure"] = ["moisture", "temperature"]
        super().__init__(*args, **kwargs)

    def _get_raw_data(self) -> tuple:
        for retry in range(3):
            try:
                moisture = self._device.moisture_read()
                temperature = self._device.get_temp()

            except RuntimeError:
                sleep(0.5)
                continue

            except Exception as e:
                sensorLogger.error(
                    f"Sensor {self._name} encountered an error. "
                    f"ERROR msg: `{e.__class__.__name__}: {e}`"
                )
                break

            else:
                return moisture, temperature

    def get_data(self) -> list[dict]:
        moisture, raw_temperature = self._get_raw_data()
        data = []
        if "moisture" in self.measure:
            data.append({"name": "moisture", "value": moisture})

        if "temperature" in self.measure:
            temperature = temperature_converter(
                raw_temperature, "celsius", self._unit
            )
            data.append({"name": "temperature", "value": temperature})
        return data


I2C_SENSORS = {
    hardware.__name__: hardware for hardware in [
        CapacitiveMoisture,
        VEML7700,
    ]
}
