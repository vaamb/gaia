from __future__ import annotations

import random
from typing import Any

from gaia.hardware._compatibility import add_noise, CompatibilityDevice, random_sleep


_BASE_TEMPERATURE = 25
_BASE_HUMIDITY = 60


class LightCompatibilityDevice(CompatibilityDevice):
    @property
    def lux(self) -> float:
        if self.virtual_ecosystem is not None:
            self.virtual_ecosystem.measure()
            return round(add_noise(self.virtual_ecosystem.light))
        return random.randrange(start=1000, stop=100000, step=10)


class TemperatureCompatibilityDevice(CompatibilityDevice):
    @property
    def temperature(self) -> float:
        if self.virtual_ecosystem is not None:
            self.virtual_ecosystem.measure()
            return round(add_noise(self.virtual_ecosystem.temperature), 2)
        return random.gauss(_BASE_TEMPERATURE, 2.5)


class HumidityCompatibilityDevice(CompatibilityDevice):
    @property
    def humidity(self) -> float:
        if self.virtual_ecosystem is not None:
            self.virtual_ecosystem.measure()
            return round(add_noise(self.virtual_ecosystem.humidity), 2)
        return random.gauss(_BASE_HUMIDITY, 5)


class MoistureCompatibilityDevice(TemperatureCompatibilityDevice, HumidityCompatibilityDevice):
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


class DHTBaseDevice(TemperatureCompatibilityDevice, HumidityCompatibilityDevice):
    def measure(self) -> None:
        random_sleep(avg_duration=0.55)


class DHT11Device(DHTBaseDevice):
    pass


class DHT22Device(DHTBaseDevice):
    pass


class AHTx0Device(TemperatureCompatibilityDevice, HumidityCompatibilityDevice):
    def _readdata(self) -> None:
        pass

    @property
    def _temp(self) -> float:
        return self.temperature

    @property
    def _humidity(self) -> float:
        return self.humidity


class VEML7700Device(LightCompatibilityDevice):
    pass


class VCNL4040Device(LightCompatibilityDevice):
    pass


class SeesawDevice(MoistureCompatibilityDevice):
    def moisture_read(self) -> float:
        random_sleep(0.02, 0.005)
        return self.moisture

    def get_temp(self) -> float:
        random_sleep(0.02, 0.005)
        return self.temperature


class ENS160Device(CompatibilityDevice):
    new_data_available = True
    data_validity = True

    def read_all_sensors(self) -> dict[str, float]:
        return {
            "AQI": random.randint(1, 5),
            "eCO2": random.gauss(600, 60),
            "TVOC": random.gauss(200, 40),
        }


class BS18B20Device(TemperatureCompatibilityDevice):
    def get_data(self) -> float | None:
        return self.temperature
