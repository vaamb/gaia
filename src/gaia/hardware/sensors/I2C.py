from __future__ import annotations

from time import sleep
import typing as t

from gaia.hardware.abc import (
    hardware_logger, i2cSensor, LightSensor, MeasureRecordDict,
    PlantLevelHardware)
from gaia.hardware.utils import _IS_RASPI
from gaia.utils import get_unit, temperature_converter


if t.TYPE_CHECKING:  # pragma: no cover
    if _IS_RASPI:
        from adafruit_ahtx0 import AHTx0
        from adafruit_seesaw.seesaw import Seesaw
        from adafruit_veml7700 import VEML7700 as _VEML7700
        from adafruit_vcnl4040 import VCNL4040 as _VCNL4040
    else:
        from gaia.hardware._compatibility import (
            AHTx0, Seesaw, VEML7700 as _VEML7700, VCNL4040 as _VCNL4040)


# ---------------------------------------------------------------------------
#   I2C sensors
# ---------------------------------------------------------------------------
class AHT20(i2cSensor):
    def __init__(self, *args, **kwargs) -> None:
        if not kwargs.get("measures"):
            kwargs["measures"] = ["temperature", "humidity"]
        super().__init__(*args, default_address=0x38, **kwargs)

    def _get_device(self) -> "AHTx0":
        if _IS_RASPI:
            try:
                from adafruit_ahtx0 import AHTx0
            except ImportError:
                raise RuntimeError(
                    "Adafruit aht0 package is required. Run `pip install "
                    "adafruit-circuitpython-ahtx0` in your virtual env."
                )
        else:
            from gaia.hardware._compatibility import AHTx0
        return AHTx0(self._get_i2c(), self._address_book.primary.main)

    def _get_raw_data(self) -> tuple[float | None, float | None]:
        try:
            self.device._readdata()
            humidity = round(self.device._humidity, 2)
            temperature = round(self.device._temp, 2)
        except Exception:
            humidity = None
            temperature = None
        return humidity, temperature

    def get_data(self) -> list[MeasureRecordDict]:
        data = []
        raw_humidity, raw_temperature = self._get_raw_data()
        if "temperature" in self.measures:
            temperature = temperature_converter(
                raw_temperature,
                "celsius",
                get_unit("temperature", "celsius")
            )
            data.append({"measure": "temperature", "value": temperature})
        if "humidity" in self.measures:
            data.append({"measure": "humidity", "value": raw_humidity})
        return data


class VEML7700(i2cSensor, LightSensor):
    def __init__(self, *args, **kwargs) -> None:
        if not kwargs.get("measures"):
            kwargs["measures"] = ["lux"]
        super().__init__(*args, default_address=0x10, **kwargs)

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
            from gaia.hardware._compatibility import VEML7700 as _VEML7700
        return _VEML7700(self._get_i2c(), self._address_book.primary.main)

    # To catch data fast from light routine
    def get_lux(self) -> float | None:
        try:
            return round(self.device.lux, 2)
        except Exception as e:
            hardware_logger.error(
                f"Sensor {self._name} encountered an error. "
                f"ERROR msg: `{e.__class__.__name__}: {e}`"
            )
            return None

    def get_data(self) -> list[MeasureRecordDict]:
        data = []
        if "lux" in self.measures or "light" in self.measures:
            data.append({"measure": "light", "value": self.get_lux()})
        return data


class VCNL4040(i2cSensor, LightSensor):
    def __init__(self, *args, **kwargs) -> None:
        if not kwargs.get("measures"):
            kwargs["measures"] = ["lux"]
        super().__init__(*args, default_address=0x60, **kwargs)

    def _get_device(self) -> "_VCNL4040":
        if _IS_RASPI:
            try:
                from adafruit_vcnl4040 import VCNL4040 as _VCNL4040
            except ImportError:
                raise RuntimeError(
                    "Adafruit vcnl4040 package is required. Run `pip install "
                    "adafruit-circuitpython-vcnl4040` in your virtual env."
                )
        else:
            from gaia.hardware._compatibility import VCNL4040 as _VCNL4040
        return _VCNL4040(self._get_i2c(), self._address_book.primary.main)

    # To catch data fast from light routine
    def get_lux(self) -> float | None:
        try:
            return round(self.device.lux, 2)
        except Exception as e:
            hardware_logger.error(
                f"Sensor {self._name} encountered an error. "
                f"ERROR msg: `{e.__class__.__name__}: {e}`"
            )
            return None

    def get_data(self) -> list[MeasureRecordDict]:
        data = []
        if "lux" in self.measures or "light" in self.measures:
            data.append({"measure": "light", "value": self.get_lux()})
        return data


class CapacitiveSensor(i2cSensor):
    def __init__(self, *args, **kwargs) -> None:
        if not kwargs.get("measures"):
            kwargs["measures"] = ["capacitive"]
        super().__init__(*args, default_address=0x36, **kwargs)

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
            from gaia.hardware._compatibility import Seesaw
        return Seesaw(self._get_i2c(), self._address_book.primary.main)

    def get_data(self) -> list[MeasureRecordDict]:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )


class CapacitiveMoisture(CapacitiveSensor, PlantLevelHardware):
    def __init__(self, *args, **kwargs) -> None:
        if not kwargs.get("measures"):
            kwargs["measures"] = ["moisture", "temperature"]
        super().__init__(*args, **kwargs)

    def _get_raw_data(self) -> tuple[float | None, float | None]:
        moisture: float | None = None
        temperature: float | None = None
        for retry in range(3):
            try:
                moisture = round(self.device.moisture_read(), 2)
                temperature = round(self.device.get_temp(), 2)

            except RuntimeError:
                sleep(0.5)
                continue

            except Exception as e:
                hardware_logger.error(
                    f"Sensor {self._name} encountered an error. "
                    f"ERROR msg: `{e.__class__.__name__}: {e}`"
                )
                break
            else:
                break
        return moisture, temperature

    def get_data(self) -> list[MeasureRecordDict]:
        try:
            moisture, raw_temperature = self._get_raw_data()
        except RuntimeError:
            moisture = raw_temperature = None
        data = []
        if "moisture" in self.measures:
            data.append({"measure": "moisture", "value": moisture})

        if "temperature" in self.measures:
            temperature = temperature_converter(
                raw_temperature, "celsius", get_unit("temperature", "celsius")
            )
            data.append({"measure": "temperature", "value": temperature})
        return data


i1c_sensor_models = {
    hardware.__name__: hardware for hardware in [
        AHT20,
        CapacitiveMoisture,
        VCNL4040,
        VEML7700,
    ]
}
