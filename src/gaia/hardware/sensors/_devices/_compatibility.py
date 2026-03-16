from __future__ import annotations

import random

from gaia.hardware._compatibility import add_noise, CompatibilityDevice, random_sleep


_BASE_TEMPERATURE = 25
_BASE_HUMIDITY = 60


class LightMixin:
    @property
    def lux(self) -> float:
        return random.randrange(start=1000, stop=100000, step=10)


class TemperatureMixin:
    @property
    def temperature(self) -> float:
        return random.gauss(_BASE_TEMPERATURE, 2.5)


class HumidityMixin:
    @property
    def humidity(self) -> float:
        return random.gauss(_BASE_HUMIDITY, 5)


class MoistureMixin:
    humidity: float
    temperature: float

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Moisture mixin should be declared after HumidityMixin and TemperatureMixin
        assert getattr(self, "humidity", None) is not None
        assert getattr(self, "temperature", None) is not None

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


class MeasureMixin:
    def measure(self) -> None:
        random_sleep(avg_duration=0.55)


class DHTBaseDevice(CompatibilityDevice, TemperatureMixin, HumidityMixin, MeasureMixin):
    pass


class DHT11Device(DHTBaseDevice):
    pass


class DHT22Device(DHTBaseDevice):
    pass


class AHTx0Device(CompatibilityDevice, TemperatureMixin, HumidityMixin):
    def _readdata(self) -> None:
        pass

    @property
    def _temp(self) -> float:
        return self.temperature

    @property
    def _humidity(self) -> float:
        return self.humidity


class VEML7700Device(CompatibilityDevice, LightMixin):
    pass


class VCNL4040Device(CompatibilityDevice, LightMixin):
    pass


class SeesawDevice(CompatibilityDevice, TemperatureMixin, HumidityMixin, MoistureMixin):
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


class BS18B20Device(CompatibilityDevice, TemperatureMixin):
    def get_data(self) -> float | None:
        return self.temperature
