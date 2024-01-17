from __future__ import annotations

import random
import time
from typing import Any

from gaia.config import get_config
from gaia.hardware.utils import hardware_logger


hardware_logger.warning(
    "The platform used is not a Raspberry Pi, using compatibility modules")


if get_config().VIRTUALIZATION:
    from gaia.virtual import get_virtual_ecosystem

    hardware_logger.info("Using ecosystem virtualization")

    def _add_noise(measure: float) -> float:
        return measure * random.gauss(1, 0.01)

    def get_humidity(ecosystem_uid: str, *args, **kwargs) -> float:
        virtual_ecosystem = get_virtual_ecosystem(ecosystem_uid)
        virtual_ecosystem.measure()
        return round(_add_noise(virtual_ecosystem.humidity), 2)

    def get_light(ecosystem_uid: str, *args, **kwargs) -> float:
        virtual_ecosystem = get_virtual_ecosystem(ecosystem_uid)
        virtual_ecosystem.measure()
        return round(_add_noise(virtual_ecosystem.lux))

    def get_moisture(ecosystem_uid: str, *args, **kwargs) -> float:
        virtual_ecosystem = get_virtual_ecosystem(ecosystem_uid)
        virtual_ecosystem.measure()
        moisture_at_42_deg = (virtual_ecosystem.humidity * 0.2) + 6
        slope = (moisture_at_42_deg - 100) / (42 - 5)
        intercept = 100 - (slope * 5)
        moisture = (slope * virtual_ecosystem.temperature) + intercept
        moisture = round(_add_noise(moisture), 2)
        if moisture > 98.3:
            moisture = 98.3
        return moisture

    def get_temperature(ecosystem_uid: str, *args, **kwargs) -> float:
        virtual_ecosystem = get_virtual_ecosystem(ecosystem_uid)
        virtual_ecosystem.measure()
        return round(_add_noise(virtual_ecosystem.temperature), 2)

else:
    _BASE_TEMPERATURE = 25
    _BASE_HUMIDITY = 60

    def get_humidity(ecosystem_uid: str, *args, **kwargs) -> float:
        return random.gauss(_BASE_HUMIDITY, 5)


    def get_light(ecosystem_uid: str, *args, **kwargs) -> float:
        return random.randrange(start=1000, stop=100000, step=10)


    def get_moisture(ecosystem_uid: str, *args, **kwargs) -> float:
        return random.gauss(_BASE_HUMIDITY/2, 5)


    def get_temperature(ecosystem_uid: str, *args, **kwargs) -> float:
        return random.gauss(_BASE_TEMPERATURE, 2.5)


def random_sleep(
        avg_duration: float = 0.55,
        std_deviation: float = 0.075
) -> None:
    if not get_config().TESTING:
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
class CompatibilityHardware:
    def __init__(self, *args, **kwargs) -> None:
        self.ecosystem_uid = kwargs.get("ecosystem_uid", "")


class LightCompatibility(CompatibilityHardware):
    @property
    def lux(self) -> float:
        random_sleep(0.02, 0.01)
        return get_light(self.ecosystem_uid)


class TemperatureCompatibility(CompatibilityHardware):
    @property
    def temperature(self) -> float:
        return get_temperature(self.ecosystem_uid)


class HumidityCompatibility(CompatibilityHardware):
    @property
    def humidity(self) -> float:
        return get_humidity(self.ecosystem_uid)


class DHTBase(TemperatureCompatibility, HumidityCompatibility):
    def measure(self) -> None:
        random_sleep()


class DHT11(DHTBase):
    pass


class DHT22(DHTBase):
    pass


class AHTx0(CompatibilityHardware):
    def _readdata(self) -> None:
        pass

    @property
    def _temp(self) -> float:
        return get_temperature(self.ecosystem_uid)

    @property
    def _humidity(self) -> float:
        return get_humidity(self.ecosystem_uid)


class VEML7700(LightCompatibility):
    pass


class VCNL4040(LightCompatibility):
    pass


class Seesaw(CompatibilityHardware):
    def moisture_read(self) -> float:
        random_sleep(0.02, 0.01)
        return get_moisture(self.ecosystem_uid)

    def get_temp(self) -> float:
        random_sleep(0.02, 0.01)
        return get_temperature(self.ecosystem_uid)


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

    def start_preview(self, preview: "Preview") -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def capture_file(self, name: str, format: str = "jpg") -> None:
        pass


class Preview:
    QTGL = None
