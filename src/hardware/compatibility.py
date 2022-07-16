import time
import warnings

from .random_measures import get_humidity, get_light, get_temperature, random_sleep


def custom_format_warning(msg, *args, **kwargs):
    return str(msg) + '\n'


format_warning = warnings.formatwarning
warnings.formatwarning = custom_format_warning

warnings.warn(
    "The platform used is not a Raspberry Pi, using compatibility modules"
)

warnings.formatwarning = format_warning


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
        self._id = bcm_nbr
        self._mode = 0
        self._value = 0

    def init(self, mode: int) -> None:
        self._mode = mode

    def value(self, val: int) -> int:
        if val:
            self._value = val
        else:
            return self._value


class VEML7700:
    def __init__(self, *args, **kwargs):
        pass

    @property
    def lux(self):
        random_sleep(0.02, 0.01)
        return get_light()


class DHTBase:
    def __init__(self, *args, **kwargs):
        self._last_called = 0
        self._temperature = 0
        self._humidity = 0

    def measure(self):
        timer = time.monotonic()
        if (timer - self._last_called) > 2:
            self._last_called = timer
            random_sleep()
            self._temperature = get_temperature()
            self._humidity = get_humidity()

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


class _basePiCamera:
    pass


class Camera:
    def __enter__(self):
        return _basePiCamera()

    def __exit__(self, *args):
        pass
