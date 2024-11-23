from __future__ import annotations

import random
import time
from typing import Any

from gaia.config import GaiaConfigHelper
from gaia.hardware.utils import hardware_logger
from gaia.subroutines.template import SubroutineTemplate
from gaia.virtual import VirtualEcosystem


hardware_logger.warning(
    "The platform used is not a Raspberry Pi, using compatibility modules.")


_BASE_TEMPERATURE = 25
_BASE_HUMIDITY = 60


def add_noise(measure: float) -> float:
    return measure * random.gauss(1, 0.01)


def random_sleep(
        avg_duration: float = 0.25,
        std_deviation: float = 0.075,
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
    """Compatibility class that implements some methods from adafruit busio
    module
    """

    class I2C:
        def __init__(self, *args, **kwargs):
            pass


class pwmio:
    """Compatibility class that implements some methods from adafruit pwmio
    module
    """

    class PWMOut:
        def __init__(self, *args, duty_cycle: int = 0, **kwargs) -> None:
            self.duty_cycle = duty_cycle


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
#   Multiplexers from Adafruit
# ---------------------------------------------------------------------------
class TCA9548A_Channel(busio.I2C):
    def __init__(self, tca: "TCA9548A", channel: int) -> None:
        super().__init__()
        self.tca = tca
        self.channel_switch = bytearray([1 << channel])


class TCA9548A:
    def __init__(self, i2c: busio.I2C, address: int = 0x70):
        self.i2c = i2c
        self.address = address
        self.channels: list[TCA9548A_Channel | None] = [None] * 8

    def __len__(self) -> int:
        return 8

    def __getitem__(self, key: int) -> TCA9548A_Channel:
        if not 0 <= key <= 7:
            raise IndexError("Channel must be an integer in the range: 0-7.")
        if self.channels[key] is None:
            self.channels[key] = TCA9548A_Channel(self, key)
        return self.channels[key]


# ---------------------------------------------------------------------------
#   Hardware from Adafruit
# ---------------------------------------------------------------------------
class CompatibilityDevice:
    def __init__(
            self,
            *args,
            subroutine: SubroutineTemplate | None = None,
            **kwargs,
    ) -> None:
        self.virtual_ecosystem: VirtualEcosystem | None
        if subroutine is not None:
            self.virtual_ecosystem = subroutine.ecosystem.virtual_self
        else:
            self.virtual_ecosystem = None


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
        r = np.random.binomial(255, 0.133, (height, width))
        g = np.random.binomial(255, 0.420, (height, width))
        b = np.random.binomial(255, 0.639, (height, width))
        array = np.stack((b, g, r), axis=2)
        return array.astype("uint8")

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
