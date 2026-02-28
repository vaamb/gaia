from __future__ import annotations

from time import sleep
import typing as t
from typing import Type

from anyio.to_thread import run_sync

from gaia.hardware.abc import (
    BaseSensor, hardware_logger, i2cSensor, LightSensor, Measure,
    PlantLevelHardware, Unit)
from gaia.hardware.sensors.abc import TempHumSensor
from gaia.hardware.utils import is_raspi
from gaia.types import SensorData
from gaia.utils import get_unit, temperature_converter


if t.TYPE_CHECKING:  # pragma: no cover
    if is_raspi():
        from adafruit_ahtx0 import AHTx0
        from adafruit_seesaw.seesaw import Seesaw
        from adafruit_veml7700 import VEML7700 as _VEML7700
        from adafruit_vcnl4040 import VCNL4040 as _VCNL4040
        from adafruit_ens160 import ENS160 as _ENS160
    else:
        from gaia.hardware._compatibility import (
            AHTx0,
            Seesaw,
            VEML7700 as _VEML7700,
            VCNL4040 as _VCNL4040,
        )


# ---------------------------------------------------------------------------
#   I2C sensors
# ---------------------------------------------------------------------------
class AHT20(TempHumSensor, i2cSensor):
    default_address = 0x38

    def _get_device(self) -> "AHTx0":
        if is_raspi():  # pragma: no cover
            try:
                from adafruit_ahtx0 import AHTx0
            except ImportError:
                raise RuntimeError(
                    "Adafruit aht0 package is required. Run `pip install "
                    "adafruit-circuitpython-ahtx0` in your virtual env."
                )
        else:
            from gaia.hardware._compatibility import AHTx0
        return AHTx0(self._get_i2c(), self.address.main)

    def _get_raw_data(self) -> tuple[float | None, float | None]:
        try:
            self.device._readdata()
            humidity = round(self.device._humidity, 2)
            temperature = round(self.device._temp, 2)
        except Exception:
            humidity = None
            temperature = None
        return humidity, temperature


class ENS160(i2cSensor):
    default_address = 0x53
    measures_available = {
        Measure.aqi: None,
        Measure.eco2: Unit.ppm,
        Measure.tvoc: Unit.ppm,
    }

    def _get_device(self) -> "_ENS160":
        if is_raspi():  # pragma: no cover
            try:
                from adafruit_ens160 import ENS160 as _ENS160
            except ImportError:
                raise RuntimeError(
                    "Adafruit ens160 package is required. Run `pip install "
                    "adafruit-circuitpython-ens160` in your virtual env."
                )
        else:
            from gaia.hardware._compatibility import ENS160 as _ENS160
        return _ENS160(self._get_i2c(), self.address.main)

    def _get_raw_data(self) -> tuple[float | None, float | None, float | None]:
        # Data status from https://github.com/adafruit/Adafruit_CircuitPython_ENS160/blob/main/adafruit_ens160.py
        # NORMAL_OP = 0x00
        # WARM_UP = 0x01
        # START_UP = 0x02
        # INVALID_OUT = 0x03
        retry = 5
        while True:
            # if no data, wait
            if self.device.new_data_available:
                break
            sleep(0.1)
            retry -= 1
            if retry <= 0:
                return None, None, None
        # If sensor's output is invalid, return None
        if self.device.data_validity == 0x03:
            return None, None, None
        data = self.device.read_all_sensors()
        # First reading is always zeroes
        if data["AQI"] == data["eCO2"] == data["TVOC"] == 0:
            return None, None, None
        return data["AQI"], data["eCO2"], data["TVOC"]

    def compensation(self, temperature: float, humidity: float) -> None:
        self.device.temperature_compensation = temperature
        self.device.humidity_compensation = humidity

    async def get_data(self) -> list[SensorData]:
        # TODO: access temperature and humidity data to compensate
        data = []
        AQI, eCO2, TVOC = await run_sync(self._get_raw_data)
        if Measure.aqi in self.measures:
            data.append(
                SensorData(
                    sensor_uid=self.uid,
                    measure=Measure.aqi.value,
                    value=AQI,
                )
            )

        if Measure.eco2 in self.measures:
            data.append(
                SensorData(
                    sensor_uid=self.uid,
                    measure=Measure.eco2.value,
                    value=eCO2,
                )
            )

        if Measure.tvoc in self.measures:
            data.append(
                SensorData(
                    sensor_uid=self.uid,
                    measure=Measure.tvoc.value,
                    value=TVOC,
                )
            )
        return data


class VEML7700(i2cSensor, LightSensor):
    default_address = 0x10
    measures_available = {
        Measure.light: Unit.lux,
    }

    def _get_device(self) -> "_VEML7700":
        if is_raspi():  # pragma: no cover
            try:
                from adafruit_veml7700 import VEML7700 as _VEML7700
            except ImportError:
                raise RuntimeError(
                    "Adafruit veml7700 package is required. Run `pip install "
                    "adafruit-circuitpython-veml7700` in your virtual env."
                )
        else:
            from gaia.hardware._compatibility import VEML7700 as _VEML7700
        return _VEML7700(self._get_i2c(), self.address.main)

    # To catch data fast from light routine
    def _get_lux(self) -> float:
        return self.device.lux

    async def get_lux(self) -> float | None:
        try:
            lux = await run_sync(self._get_lux)
            return round(lux, 2)
        except Exception as e:
            hardware_logger.error(
                f"Sensor {self._name} encountered an error. "
                f"ERROR msg: `{e.__class__.__name__}: {e}`."
            )
            return None

    async def get_data(self) -> list[SensorData]:
        data = []
        if Measure.light in self.measures:
            data.append(
                SensorData(
                    sensor_uid=self.uid,
                    measure=Measure.light.value,
                    value=await self.get_lux(),
                )
            )
        return data


class VCNL4040(i2cSensor, LightSensor):
    default_address = 0x60
    measures_available = {
        Measure.light: Unit.lux,
    }

    def _get_device(self) -> "_VCNL4040":
        if is_raspi():  # pragma: no cover
            try:
                from adafruit_vcnl4040 import VCNL4040 as _VCNL4040
            except ImportError:
                raise RuntimeError(
                    "Adafruit vcnl4040 package is required. Run `pip install "
                    "adafruit-circuitpython-vcnl4040` in your virtual env."
                )
        else:
            from gaia.hardware._compatibility import VCNL4040 as _VCNL4040
        return _VCNL4040(self._get_i2c(), self.address.main)

    # To catch data fast from light routine
    def _get_lux(self) -> float:
        return self.device.lux

    async def get_lux(self) -> float | None:
        try:
            lux = await run_sync(self._get_lux)
            return round(lux, 2)
        except Exception as e:
            hardware_logger.error(
                f"Sensor {self._name} encountered an error. "
                f"ERROR msg: `{e.__class__.__name__}: {e}`."
            )
            return None

    async def get_data(self) -> list[SensorData]:
        data = []
        if Measure.light in self.measures:
            data.append(
                SensorData(
                    sensor_uid=self.uid,
                    measure=Measure.light.value,
                    value=await self.get_lux(),
                )
            )
        return data


class CapacitiveSensor(i2cSensor):
    default_address = 0x36
    measures_available = {
        Measure.capacitive: None,
    }

    def _get_device(self) -> "Seesaw":
        if is_raspi():  # pragma: no cover
            try:
                from adafruit_seesaw.seesaw import Seesaw
            except ImportError:
                raise RuntimeError(
                    "Adafruit seesaw package is required. Run `pip install "
                    "adafruit-circuitpython-seesaw` in your virtual env."
                )
        else:
            from gaia.hardware._compatibility import Seesaw
        return Seesaw(self._get_i2c(), self.address.main)

    async def get_data(self) -> list[SensorData]:
        raise NotImplementedError("This method must be implemented in a subclass")


class CapacitiveMoisture(CapacitiveSensor, PlantLevelHardware):
    measures_available = {
        Measure.moisture: Unit.RWC,
        Measure.temperature: Unit.celsius_degree,
    }

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
                    f"ERROR msg: `{e.__class__.__name__}: {e}`."
                )
                break
            else:
                break
        return moisture, temperature

    async def get_data(self) -> list[SensorData]:
        try:
            moisture, raw_temperature = await run_sync(self._get_raw_data)
        except RuntimeError:
            moisture = raw_temperature = None
        data = []
        if Measure.moisture in self.measures:
            data.append(
                SensorData(
                    sensor_uid=self.uid,
                    measure=Measure.moisture.value,
                    value=moisture,
                )
            )

        if Measure.temperature in self.measures:
            temperature = temperature_converter(
                raw_temperature, "celsius", get_unit("temperature", "celsius")
            )
            data.append(
                SensorData(
                    sensor_uid=self.uid,
                    measure=Measure.temperature.value,
                    value=temperature,
                )
            )
        return data


i2c_sensor_models: dict[str, Type[BaseSensor]] = {
    hardware.__name__: hardware
    for hardware in [
        AHT20,
        CapacitiveMoisture,
        ENS160,
        VCNL4040,
        VEML7700,
    ]
}
