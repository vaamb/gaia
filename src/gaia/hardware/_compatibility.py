from __future__ import annotations

import random
import time
from typing import Any

from gaia.config import GaiaConfigHelper
from gaia.hardware.utils import hardware_logger
from gaia.virtual import VirtualEcosystem


hardware_logger.warning(
    "The platform used is not a Raspberry Pi, using compatibility modules")


_BASE_TEMPERATURE = 25
_BASE_HUMIDITY = 60


def add_noise(measure: float) -> float:
    return measure * random.gauss(1, 0.01)


def random_sleep(
        avg_duration: float = 0.25,
        std_deviation: float = 0.075
) -> None:
    if not GaiaConfigHelper.get_config().TESTING:
        time.sleep(abs(random.gauss(avg_duration, std_deviation)))


# ---------------------------------------------------------------------------
#   Raspberry Pi modules from Adafruit
# ---------------------------------------------------------------------------
class board:
    SCL = None
    SDA = None


class busio:
    """ Compatibility class that implements some methods from adafruit busio
    module
    """
    @staticmethod
    def I2C(*args, **kwargs) -> None:
        return None


class pwmio:
    """ Compatibility class that implements some methods from adafruit pwmio
    module
    """
    class PWMOut:
        def __init__(self, *args, **kwargs) -> None:
            duty_cycle = 0


class Pin:
    def __init__(self, bcm_nbr: int) -> None:
        self._id: int = bcm_nbr
        self._mode: int = 0
        self._value: int = 0

    def init(self, mode: int) -> None:
        self._mode = mode

    def value(self, val: int | None = None) -> int | None:
        if val is None:
            return self._value
        self._value = val


# ---------------------------------------------------------------------------
#   Hardware modules from Adafruit
# ---------------------------------------------------------------------------
class CompatibilityDevice:
    def __init__(
            self,
            *args,
            virtual_ecosystem: VirtualEcosystem | None = None,
            **kwargs
    ) -> None:
        self.virtual_ecosystem: VirtualEcosystem | None = virtual_ecosystem


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
            return round(add_noise(self.virtual_ecosystem.light), 2)
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


class PiCamera:
    def create_preview_configuration(self) -> None:
        pass

    def create_still_configuration(self) -> None:
        pass

    def create_video_configuration(self) -> None:
        pass

    def capture_array(self, *args) -> Any:
        pass

    def configure(self, camera_config) -> Any:
        pass

    def start_preview(self, preview: Preview) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def capture_file(self, name: str, format: str = "jpg") -> None:
        pass


class Preview:
    QTGL = None
