import random
import time
import warnings

from gaia.config import get_config
from gaia.hardware.utils import _IS_RASPI


if not _IS_RASPI:
    def custom_format_warning(msg, *args, **kwargs):
        return str(msg) + '\n'

    format_warning = warnings.formatwarning
    warnings.formatwarning = custom_format_warning
    warnings.warn(
        "The platform used is not a Raspberry Pi, using compatibility modules"
    )
    warnings.formatwarning = format_warning


if get_config().VIRTUALIZATION:
    from gaia.virtual import get_virtual_ecosystem

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
        return round(random.uniform(10, 55), 2)

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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_called = 0
        self._temperature = 0
        self._humidity = 0

    def measure(self):
        timer = time.monotonic()
        if (timer - self._last_called) > 2:
            self._last_called = timer
            random_sleep()
            self._temperature = get_temperature(self.ecosystem_uid)
            self._humidity = get_humidity(self.ecosystem_uid)

    @property
    def temperature(self):
        return self._temperature

    @property
    def humidity(self):
        return self._humidity


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
