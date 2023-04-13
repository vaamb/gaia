import typing as t
from typing import Self

from gaia.hardware.utils import _IS_RASPI, get_i2c


if t.TYPE_CHECKING:  # pragma: no cover
    if _IS_RASPI:
        from adafruit_tca9548a import TCA9548A as tca


class _MetaMultiplexer(type):
    instances: dict[str, Self] = {}

    def __call__(cls, *args, **kwargs):
        address = kwargs["address"]
        try:
            return cls.instances[address]
        except KeyError:
            multiplexer = cls.__new__(cls, *args, **kwargs)
            multiplexer.__init__(*args, **kwargs)
            cls.instances[address] = multiplexer
            return multiplexer


class Multiplexer(metaclass=_MetaMultiplexer):
    def __init__(self, address, i2c=None):
        if i2c is None:
            self._i2c = get_i2c()
        else:
            self._i2c = i2c
        self._address = address
        self.device = self._get_device()

    def __del__(self):
        del _MetaMultiplexer.instances[self._address]

    def _get_device(self):
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    @property
    def address(self) -> int:
        return self._address

    def get_channel(self, number):
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover


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

    def get_channel(self, number):
        return self.device[number]


multiplexer_models = {
    hardware.__name__: hardware for hardware in [
        TCA9548A,
    ]
}
