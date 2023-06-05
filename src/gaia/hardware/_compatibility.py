import random
import time

from gaia.config import get_config
from gaia.hardware.utils import _IS_RASPI, hardware_logger


if not _IS_RASPI:
    hardware_logger.warning(
        "The platform used is not a Raspberry Pi, using compatibility modules")
else:
    hardware_logger.warning(
        "hardware._compatibility module has been loaded although the platform "
        "used is a Raspberry Pi")


if get_config().VIRTUALIZATION:
    from gaia.virtual import get_virtual_ecosystem

    hardware_logger.info("Using ecosystem virtualization")

    def _add_noise(measure):
        return measure * random.gauss(1, 0.01)

    def get_humidity(ecosystem_uid, *args, **kwargs) -> float:
        virtual_ecosystem = get_virtual_ecosystem(ecosystem_uid, start=True)
        virtual_ecosystem.measure()
        return round(_add_noise(virtual_ecosystem.humidity), 2)

    def get_light(ecosystem_uid, *args, **kwargs) -> float:
        virtual_ecosystem = get_virtual_ecosystem(ecosystem_uid, start=True)
        virtual_ecosystem.measure()
        return round(_add_noise(virtual_ecosystem.lux))

    def get_moisture(ecosystem_uid, *args, **kwargs) -> float:
        virtual_ecosystem = get_virtual_ecosystem(ecosystem_uid, start=True)
        virtual_ecosystem.measure()
        moisture_at_42_deg = (virtual_ecosystem.humidity * 0.2) + 6
        slope = (moisture_at_42_deg - 100) / (42 - 5)
        intercept = 100 - (slope * 5)
        moisture = (slope * virtual_ecosystem.temperature) + intercept
        if moisture > 98.3:
            moisture = 98.3
        return round(_add_noise(moisture), 2)

    def get_temperature(ecosystem_uid, *args, **kwargs) -> float:
        virtual_ecosystem = get_virtual_ecosystem(ecosystem_uid, start=True)
        virtual_ecosystem.measure()
        return round(_add_noise(virtual_ecosystem.temperature), 2)

else:
    _BASE_TEMPERATURE = 25
    _BASE_HUMIDITY = 60

    def get_humidity(*args, **kwargs) -> float:
        return random.gauss(_BASE_HUMIDITY, 5)


    def get_light(*args, **kwargs) -> float:
        return random.randrange(start=1000, stop=100000, step=10)


    def get_moisture(*args, **kwargs) -> float:
        return random.gauss(_BASE_HUMIDITY/2, 5)


    def get_temperature(*args, **kwargs) -> float:
        return random.gauss(_BASE_TEMPERATURE, 2.5)


def random_sleep(
        avg_duration: float = 0.15,
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
    def I2C(*args, **kwargs):
        return None


class pwmio:
    """ Compatibility class that implements some methods from adafruit pwmio
    module
    """
    class PWMOut:
        def __init__(self, *args, **kwargs):
            duty_cycle = 0


class Pin:
    def __init__(self, bcm_nbr: int) -> None:
        self._id: int = bcm_nbr
        self._mode: int = 0
        self._value: int = 0

    def init(self, mode: int) -> None:
        self._mode = mode

    def value(self, val: int) -> int:
        if val:
            self._value = val
        else:
            return self._value


# ---------------------------------------------------------------------------
#   Hardware modules from Adafruit
# ---------------------------------------------------------------------------
class CompatibilityHardware:
    def __init__(self, *args, **kwargs):
        self.ecosystem_uid = kwargs.pop("ecosystem_uid", "")


class DHTBase(CompatibilityHardware):
    def measure(self):
        random_sleep()

    @property
    def temperature(self):
        return get_temperature(self.ecosystem_uid)

    @property
    def humidity(self):
        return get_humidity(self.ecosystem_uid)


class DHT11(DHTBase):
    pass


class DHT22(DHTBase):
    pass


class VEML7700(CompatibilityHardware):
    @property
    def lux(self):
        random_sleep(0.02, 0.01)
        return get_light(self.ecosystem_uid)


class Seesaw(CompatibilityHardware):
    def moisture_read(self) -> float:
        random_sleep(0.02, 0.01)
        return get_moisture(self.ecosystem_uid)

    def get_temp(self) -> float:
        random_sleep(0.02, 0.01)
        return get_temperature(self.ecosystem_uid)


class PiCamera:
    def create_preview_configuration(self):
        pass

    def create_still_configuration(self):
        pass

    def create_video_configuration(self):
        pass

    def capture_array(self, *args):
        pass

    def configure(self, camera_config):
        pass

    def start_preview(self, preview: "Preview"):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def capture_file(self, name, format="jpg"):
        pass


class Preview:
    QTGL = None
