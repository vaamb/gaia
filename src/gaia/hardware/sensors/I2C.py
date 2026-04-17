from __future__ import annotations

from time import sleep
import typing as t
from typing import Type

from anyio.to_thread import run_sync

from gaia.hardware.abc import (
    hardware_logger, i2cAddressMixin, Measure, PlantLevelMixin,
    Sensor, SensorRead, Unit)
from gaia.hardware.sensors.abc import LightSensorBase, TempHumSensor
from gaia.hardware.utils import is_raspi
from gaia.utils import get_unit, temperature_converter


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.hardware.sensors._devices._compatibility import (
        AHTx0Device,
        SeesawDevice,
        VEML7700Device,
        VCNL4040Device,
        ENS160Device,
    )


# ---------------------------------------------------------------------------
#   I2C sensors
# ---------------------------------------------------------------------------
class i2cSensor(i2cAddressMixin, Sensor):
    ...


class AHT20(TempHumSensor, i2cSensor):
    default_address = 0x38

    def _get_device(self) -> AHTx0Device:
        if is_raspi():  # pragma: no cover
            try:
                from adafruit_ahtx0 import AHTx0 as AHTx0Device  # ty: ignore[unresolved-import]
            except ImportError:
                raise RuntimeError(
                    "Adafruit aht0 package is required. Run `pip install "
                    "adafruit-circuitpython-ahtx0` in your virtual env."
                )
        else:
            from gaia.hardware.sensors._devices._compatibility import AHTx0Device
        return AHTx0Device(self._get_i2c(), self.address.main)

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

    def _get_device(self) -> ENS160Device:
        if is_raspi():  # pragma: no cover
            try:
                from adafruit_ens160 import ENS160 as ENS160Device  # ty: ignore[unresolved-import]
            except ImportError:
                raise RuntimeError(
                    "Adafruit ens160 package is required. Run `pip install "
                    "adafruit-circuitpython-ens160` in your virtual env."
                )
        else:
            from gaia.hardware.sensors._devices._compatibility import ENS160Device
        return ENS160Device(self._get_i2c(), self.address.main)

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

    async def get_data(self) -> list[SensorRead]:
        # TODO: access temperature and humidity data to compensate
        data = []
        AQI, eCO2, TVOC = await run_sync(self._get_raw_data)
        if Measure.aqi in self.measures:
            data.append(
                SensorRead(
                    sensor_uid=self.uid,
                    measure=Measure.aqi.value,
                    value=AQI,
                )
            )

        if Measure.eco2 in self.measures:
            data.append(
                SensorRead(
                    sensor_uid=self.uid,
                    measure=Measure.eco2.value,
                    value=eCO2,
                )
            )

        if Measure.tvoc in self.measures:
            data.append(
                SensorRead(
                    sensor_uid=self.uid,
                    measure=Measure.tvoc.value,
                    value=TVOC,
                )
            )
        return data


class VEML7700(LightSensorBase, i2cSensor):
    default_address = 0x10
    measures_available = {
        Measure.light: Unit.lux,
    }

    def _get_device(self) -> VEML7700Device:
        if is_raspi():  # pragma: no cover
            try:
                from adafruit_veml7700 import VEML7700 as VEML7700Device  # ty: ignore[unresolved-import]
            except ImportError:
                raise RuntimeError(
                    "Adafruit veml7700 package is required. Run `pip install "
                    "adafruit-circuitpython-veml7700` in your virtual env."
                )
        else:
            from gaia.hardware.sensors._devices._compatibility import VEML7700Device
        return VEML7700Device(self._get_i2c(), self.address.main)

    def _get_lux(self) -> float:
        return self.device.lux


class VCNL4040(LightSensorBase, i2cSensor):
    default_address = 0x60
    measures_available = {
        Measure.light: Unit.lux,
    }

    def _get_device(self) -> VCNL4040Device:
        if is_raspi():  # pragma: no cover
            try:
                from adafruit_vcnl4040 import VCNL4040 as VCNL4040Device  # ty: ignore[unresolved-import]
            except ImportError:
                raise RuntimeError(
                    "Adafruit vcnl4040 package is required. Run `pip install "
                    "adafruit-circuitpython-vcnl4040` in your virtual env."
                )
        else:
            from gaia.hardware.sensors._devices._compatibility import VCNL4040Device
        return VCNL4040Device(self._get_i2c(), self.address.main)

    def _get_lux(self) -> float:
        return self.device.lux


class CapacitiveSensorMixin(i2cSensor):
    default_address = 0x36
    measures_available = {
        Measure.capacitive: None,
    }

    def _get_device(self) -> SeesawDevice:
        if is_raspi():  # pragma: no cover
            try:
                from adafruit_seesaw.seesaw import Seesaw  as SeesawDevice # ty: ignore[unresolved-import]
            except ImportError:
                raise RuntimeError(
                    "Adafruit seesaw package is required. Run `pip install "
                    "adafruit-circuitpython-seesaw` in your virtual env."
                )
        else:
            from gaia.hardware.sensors._devices._compatibility import SeesawDevice
        return SeesawDevice(self._get_i2c(), self.address.main)


class CapacitiveMoisture(PlantLevelMixin, CapacitiveSensorMixin):
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

    async def get_data(self) -> list[SensorRead]:
        try:
            moisture, raw_temperature = await run_sync(self._get_raw_data)
        except RuntimeError:
            moisture = raw_temperature = None
        data = []
        if Measure.moisture in self.measures:
            data.append(
                SensorRead(
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
                SensorRead(
                    sensor_uid=self.uid,
                    measure=Measure.temperature.value,
                    value=temperature,
                )
            )
        return data


i2c_sensor_models: dict[str, Type[i2cSensor]] = {
    hardware.__name__: hardware
    for hardware in [
        AHT20,
        CapacitiveMoisture,
        ENS160,
        VCNL4040,
        VEML7700,
    ]
}
