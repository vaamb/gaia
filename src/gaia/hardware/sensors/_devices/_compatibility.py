from __future__ import annotations

import random
from typing import Any

from gaia.hardware._compatibility import add_noise, CompatibilityDevice, random_sleep


_BASE_TEMPERATURE = 25
_BASE_HUMIDITY = 60


class LightCompatibility(CompatibilityDevice):
    @property
    def lux(self) -> float:
        if self.virtual_ecosystem is not None:
            self.virtual_ecosystem.measure()
            return round(add_noise(self.virtual_ecosystem.light))
        return random.randrange(start=1000, stop=100000, step=10)


class TemperatureCompatibility(CompatibilityDevice):
    @property
    def temperature(self) -> float:
        if self.virtual_ecosystem is not None:
            self.virtual_ecosystem.measure()
            return round(add_noise(self.virtual_ecosystem.temperature), 2)
        return random.gauss(_BASE_TEMPERATURE, 2.5)


class HumidityCompatibility(CompatibilityDevice):
    @property
    def humidity(self) -> float:
        if self.virtual_ecosystem is not None:
            self.virtual_ecosystem.measure()
            return round(add_noise(self.virtual_ecosystem.humidity), 2)
        return random.gauss(_BASE_HUMIDITY, 5)


class MoistureCompatibility(TemperatureCompatibility, HumidityCompatibility):
    @property
    def moisture(self) -> float:
        moisture_at_42_deg = (self.humidity * 0.2) + 6
        slope = (moisture_at_42_deg - 100) / (42 - 5)
        intercept = 100 - (slope * 5)
        moisture = (slope * self.temperature) + intercept
        moisture = round(add_noise(moisture), 2)
        if moisture > 98.3:
            moisture = 98.3
        return moisture


class DHTBase(TemperatureCompatibility, HumidityCompatibility):
    def measure(self) -> None:
        random_sleep(avg_duration=0.55)


class DHT11(DHTBase):
    pass


class DHT22(DHTBase):
    pass


class AHTx0(TemperatureCompatibility, HumidityCompatibility):
    def _readdata(self) -> None:
        pass

    @property
    def _temp(self) -> float:
        return self.temperature

    @property
    def _humidity(self) -> float:
        return self.humidity


class VEML7700(LightCompatibility):
    pass


class VCNL4040(LightCompatibility):
    pass


class Seesaw(MoistureCompatibility):
    def moisture_read(self) -> float:
        random_sleep(0.02, 0.005)
        return self.moisture

    def get_temp(self) -> float:
        random_sleep(0.02, 0.005)
        return self.temperature


class ENS160(CompatibilityDevice):
    new_data_available = True
    data_validity = True

    def read_all_sensors(self) -> dict[str, float]:
        return {
            "AQI": random.randint(1, 5),
            "eCO2": random.gauss(600, 60),
            "TVOC": random.gauss(200, 40),
        }


class BS18B20(TemperatureCompatibility):
    def get_data(self) -> float | None:
        return self.temperature


class Picamera2:
    def __init__(self):
        self._cfg: dict = {"size": (800, 600)}

    def create_preview_configuration(self, main={}, *args, **kwargs) -> dict:
        return {"size": (800, 600), **main}

    def create_still_configuration(self, main={}, *args, **kwargs) -> dict:
        return {"size": (800, 600), **main}

    def create_video_configuration(self, main={}, *args, **kwargs) -> dict:
        return {"size": (800, 600), **main}

    def capture_array(self, name="main") -> Any:
        import numpy as np

        width, height = self._cfg["size"]
        array = np.stack(
            (
                np.random.binomial(255, 0.639, (height, width)).astype("uint8"),  #b
                np.random.binomial(255, 0.420, (height, width)).astype("uint8"),  #g
                np.random.binomial(255, 0.133, (height, width)).astype("uint8"),  #r
            ),
            axis=2,
        )
        return array

    def configure(self, camera_config: dict | str) -> None:
        if isinstance(camera_config, dict):
            self._cfg.update(camera_config)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def capture_file(self, name: str, format: str = "jpg") -> None:
        pass

    def close(self) -> None:
        pass
