from __future__ import annotations

from time import sleep
import typing as t

from . import _IS_RASPI
from .ABC import i2cSensor, PlantLevelHardware, sensorLogger
from ..utils import temperature_converter


if t.TYPE_CHECKING:  # pragma: no cover
    if _IS_RASPI:
        from adafruit_veml7700 import VEML7700 as _VEML7700
        from adafruit_seesaw.seesaw import Seesaw
    else:
        from ._compatibility import (
            DHT11 as _DHT11, DHT22 as _DHT22, Seesaw, VEML7700 as _VEML7700
        )


# ---------------------------------------------------------------------------
#   I2C sensors
# ---------------------------------------------------------------------------
class VEML7700(i2cSensor):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not kwargs.get("measure", ()):
            kwargs["measure"] = ["lux"]
        if not self._address["main"].main:
            self._address["main"].main = 0x10
        self._device = self._get_device()

    def _get_device(self) -> "_VEML7700":
        if _IS_RASPI:
            try:
                from adafruit_veml7700 import VEML7700 as _VEML7700
            except ImportError:
                raise RuntimeError(
                    "Adafruit veml7700 package is required. Run `pip install "
                    "adafruit-circuitpython-veml7700` in your virtual env."
                )
        else:
            from ._compatibility import VEML7700 as _VEML7700
        return _VEML7700(self._get_i2c(), self._address["main"].main)

    # To catch data fast from light routine
    def _get_lux(self) -> float | None:
        try:
            return self._device.lux
        except Exception as e:
            sensorLogger.error(
                f"Sensor {self._name} encountered an error. "
                f"ERROR msg: `{e.__class__.__name__}: {e}`"
            )
            return None

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
        if not self._address["main"].main:
            self._address["main"].main = 0x36
        self._unit = kwargs.pop("unit", "celsius")
        self._device = self._get_device()

    def _get_device(self) -> "Seesaw":
        if _IS_RASPI:
            try:
                from adafruit_seesaw.seesaw import Seesaw
            except ImportError:
                raise RuntimeError(
                    "Adafruit seesaw package is required. Run `pip install "
                    "adafruit-circuitpython-seesaw` in your virtual env."
                )
        else:
            from ._compatibility import Seesaw
        return Seesaw(self._get_i2c(), self._address["main"].main)

    def get_data(self) -> list[dict]:  # pragma: no cover
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )


class CapacitiveMoisture(CapacitiveSensor, PlantLevelHardware):
    def __init__(self, *args, **kwargs) -> None:
        if not kwargs.get("measure", ()):
            kwargs["measure"] = ["moisture", "temperature"]
        super().__init__(*args, **kwargs)

    def _get_raw_data(self) -> tuple[float | None, float | None]:
        moisture: float | None = None
        temperature: float | None = None
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
                break
        return moisture, temperature

    def get_data(self) -> list[dict]:
        try:
            moisture, raw_temperature = self._get_raw_data()
        except RuntimeError:
            moisture = raw_temperature = None
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
