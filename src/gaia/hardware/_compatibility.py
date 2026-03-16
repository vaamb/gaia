from __future__ import annotations

import random
import time

from gaia.config import GaiaConfigHelper
from gaia.ecosystem import Ecosystem
from gaia.hardware.utils import hardware_logger
from gaia.virtual import VirtualEcosystem


hardware_logger.warning(
    "The platform used is not a Raspberry Pi, using compatibility modules.")


def add_noise(measure: float) -> float:
    return measure * random.gauss(1, 0.001)


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


class CompatibilityDevice:
    def __init__(self, *args, **kwargs) -> None:
        pass
