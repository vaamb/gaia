from __future__ import annotations

from time import sleep
import typing as t

from gaia.hardware.abc import gpioSensor, hardware_logger, MeasureRecordDict
from gaia.hardware.utils import _IS_RASPI
from gaia.utils import (
    get_absolute_humidity, get_dew_point, get_unit, temperature_converter)


if t.TYPE_CHECKING:  # pragma: no cover
    if _IS_RASPI:
        from adafruit_dht import DHT11 as _DHT11, DHT22 as _DHT22
    else:
        from gaia.hardware._compatibility import (
            DHT11 as _DHT11, DHT22 as _DHT22)


# ---------------------------------------------------------------------------
#   GPIO sensors
# ---------------------------------------------------------------------------
class DHTSensor(gpioSensor):
    def __init__(self, *args, **kwargs) -> None:
        if not kwargs.get("measures"):
            kwargs["measures"] = ["temperature", "humidity"]
        super().__init__(*args, **kwargs)
        # Load dht device.
        # Rem: don't use pulseio as it uses 100% of one core in Pi3
        # In Pi0: behaves correctly

    def _get_raw_data(self) -> tuple[float | None, float | None]:
        humidity: float | None = None
        temperature: float | None = None
        for retry in range(3):
            try:
                self.device.measure()
                humidity = round(self.device.humidity, 2)
                temperature = round(self.device.temperature, 2)

            except RuntimeError:
                sleep(0.5)

            except Exception as e:
                hardware_logger.error(
                    f"Sensor {self._name} encountered an error. "
                    f"ERROR msg: `{e.__class__.__name__}: {e}`"
                )
                sleep(0.5)

            else:
                break
        return humidity, temperature

    def get_data(self) -> list[MeasureRecordDict]:
        try:
            raw_humidity, raw_temperature = self._get_raw_data()
        except RuntimeError:
            raw_humidity = raw_temperature = None
        data = []
        if raw_humidity is not None and raw_temperature is not None:
            if "humidity" in self.measures:
                data.append({"measure": "humidity", "value": raw_humidity})

            if "temperature" in self.measures:
                temperature = temperature_converter(
                    raw_temperature, "celsius", get_unit("temperature", "celsius")
                )
                data.append({"measure": "temperature", "value": temperature})

            if "dew_point" in self.measures:
                raw_dew_point = get_dew_point(raw_temperature, raw_humidity)
                dew_point = temperature_converter(
                    raw_dew_point, "celsius", get_unit("temperature", "celsius")
                )
                data.append({"measure": "dew_point", "value": dew_point})

            if "absolute_humidity" in self.measures:
                raw_absolute_humidity = get_absolute_humidity(
                    raw_temperature, raw_humidity)
                data.append({"measure": "absolute_humidity", "value": raw_absolute_humidity})
        return data


class DHT11(DHTSensor):
    def _get_device(self) -> "_DHT11":
        if _IS_RASPI:
            try:
                from adafruit_dht import DHT11 as _DHT11
            except ImportError:
                raise RuntimeError(
                    "Adafruit dht package and libgpiod2 are required. Run "
                    "`pip install adafruit-circuitpython-dht` in your "
                    "virtual env and `sudo apt install libgpiod2`."
                )
        else:
            from gaia.hardware._compatibility import DHT11 as _DHT11
        return _DHT11(self.pin, use_pulseio=False)


class DHT22(DHTSensor):
    def _get_device(self) -> "_DHT22":
        if _IS_RASPI:
            try:
                from adafruit_dht import DHT22 as _DHT22
            except ImportError:
                raise RuntimeError(
                    "Adafruit dht package and libgpiod2 are required. Run "
                    "`pip install adafruit-circuitpython-dht` in your "
                    "virtual env and `sudo apt install libgpiod2`."
                )
        else:
            from gaia.hardware._compatibility import DHT22 as _DHT22
        return _DHT22(self.pin, use_pulseio=False)


gpio_sensor_models = {
    hardware.__name__: hardware for hardware in [
        DHT11,
        DHT22,
    ]
}
