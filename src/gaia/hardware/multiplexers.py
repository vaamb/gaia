from __future__ import annotations

import typing as t
from typing import Any

import busio

from gaia.hardware.utils import get_i2c, is_raspi


if t.TYPE_CHECKING:  # pragma: no cover
    if is_raspi():
        from adafruit_tca9548a import TCA9548A as tca


class _MetaMultiplexer(type):
    instances: dict[str, "Multiplexer"] = {}

    def __call__(cls, *args, **kwargs) -> "Multiplexer":
        address: int = kwargs["address"]
        str_address = str(address)
        try:
            return cls.instances[str_address]
        except KeyError:
            multiplexer: "Multiplexer" = cls.__new__(cls, *args, **kwargs)
            multiplexer.__init__(*args, **kwargs)
            cls.instances[str_address] = multiplexer
            return multiplexer


class Multiplexer(metaclass=_MetaMultiplexer):
    def __init__(self, i2c_address: int, i2c: None | busio.I2C=None) -> None:
        if i2c is None:
            self._i2c = get_i2c()
        else:
            self._i2c = i2c
        self._address: int = i2c_address
        self.device = self._get_device()

    def __del__(self):
        str_address = str(self._address)
        del _MetaMultiplexer.instances[str_address]

    def _get_device(self) -> Any:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    @property
    def address(self) -> int:
        return self._address

    def get_channel(self, number: int):
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover


class TCA9548A(Multiplexer):
    def __init__(self, i2c=None, i2c_address=0x70):
        super(TCA9548A, self).__init__(i2c_address, i2c)

    def _get_device(self) -> "tca":
        if is_raspi():
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

    def get_channel(self, number: int) -> Any:
        return self.device[number]


multiplexer_models = {
    hardware.__name__: hardware for hardware in [
        TCA9548A,
    ]
}
