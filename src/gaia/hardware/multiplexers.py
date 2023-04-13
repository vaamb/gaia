import typing as t
from typing import Any

from gaia.hardware.utils import _IS_RASPI, get_i2c


if t.TYPE_CHECKING:  # pragma: no cover
    if _IS_RASPI:
        from adafruit_tca9548a import TCA9548A as tca


_store: dict[str, Any] = {}


def get_multiplexer(multiplexer_address) -> "Multiplexer":
    try:
        return _store[multiplexer_address]
    except KeyError:
        multiplexer = TCA9548A()
        _store[multiplexer_address] = multiplexer  # TODO later: find a way to indicate proper class
        return multiplexer


class Multiplexer:
    def __init__(self, address, i2c=None):
        if i2c is None:
            self._i2c = get_i2c()
        else:
            self._i2c = i2c
        self._address = address
        self._device = self._get_device()

    def _get_device(self):
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover

    def get_channel(self, number):
        return self._device[number]


class TCA9548A(Multiplexer):
    def __init__(self, i2c=None, address=0x70):
        super(TCA9548A, self).__init__(address, i2c)

    def _get_device(self) -> "tca":
        if _IS_RASPI:
            try:
                from adafruit_tca9548a import TCA9548A as tca
            except ImportError:
                raise RuntimeError(
                    "Adafruit tca9548a and busdevice packages are required. "
                    "Run `pip install adafruit-circuitpython-tca9548a` and "
                    "`pip install adafruit-circuitpython-busdevice` "
                    "in your virtual env."
                )
        else:
            raise RuntimeError(
                "TCA9548A has not been implemented for non Raspi computer (yet)"
            )
        return tca(get_i2c(), self._address)


multiplexer_models = {
    hardware.__name__: hardware for hardware in [
        TCA9548A,
    ]
}
