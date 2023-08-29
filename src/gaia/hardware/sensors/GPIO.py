from __future__ import annotations

from time import sleep
import typing as t

from gaia.hardware.abc import gpioSensor, hardware_logger
from gaia.hardware.sensors.abc import TempHumSensor
from gaia.hardware.utils import _IS_RASPI


if t.TYPE_CHECKING:  # pragma: no cover
    if _IS_RASPI:
        from adafruit_dht import DHT11 as _DHT11, DHT22 as _DHT22
    else:
        from gaia.hardware._compatibility import (
            DHT11 as _DHT11, DHT22 as _DHT22)


# ---------------------------------------------------------------------------
#   GPIO sensors
# ---------------------------------------------------------------------------
class DHTSensor(TempHumSensor):
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
