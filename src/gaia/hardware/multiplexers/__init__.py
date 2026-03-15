from __future__ import annotations

import typing as t
from typing import Any
from weakref import WeakValueDictionary

import busio  # TODO: maybe use the compatibility module ?

from gaia.hardware.utils import get_i2c, is_raspi


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.hardware.multiplexers._devices._compatibility import TCA9548ADevice as _TCA9548A


class _MetaMultiplexer(type):
    instances: WeakValueDictionary[str, Multiplexer] = WeakValueDictionary()

    def __call__(cls, *args, **kwargs) -> Multiplexer:
        address: int = kwargs["i2c_address"]
        str_address = str(address)
        try:
            return cls.instances[str_address]
        except KeyError:
            multiplexer: Multiplexer = cls.__new__(cls, *args, **kwargs)  # ty: ignore[invalid-assignment]
            multiplexer.__init__(*args, **kwargs)
            cls.instances[str_address] = multiplexer
            return multiplexer


class Multiplexer(metaclass=_MetaMultiplexer):
    __slots__ = ("_address", "_i2c", "device")

    def __init__(self, i2c_address: int, i2c: None | busio.I2C = None) -> None:
        if i2c is None:
            self._i2c = get_i2c()
        else:
            self._i2c = i2c
        self._address: int = i2c_address
        self.device = self._get_device()

    def _get_device(self) -> Any:
        raise NotImplementedError("This method must be implemented in a subclass")

    @property
    def address(self) -> int:
        return self._address

    def get_channel(self, number: int):
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover


class TCA9548A(Multiplexer):
    def __init__(self, i2c_address=0x70, i2c: None | busio.I2C = None) -> None:
        super().__init__(i2c_address, i2c)

    def _get_device(self) -> _TCA9548A:
        if is_raspi():  # pragma: no cover
            try:
                from adafruit_tca9548a import TCA9548A as _TCA9548A  # ty: ignore[unresolved-import]
            except ImportError:
                raise RuntimeError(
                    "Adafruit tca9548a and busdevice packages are required. "
                    "Run `pip install adafruit-circuitpython-tca9548a` and "
                    "`pip install adafruit-circuitpython-busdevice` "
                    "in your virtual env."
                )
        else:
            from gaia.hardware.multiplexers._devices._compatibility import TCA9548ADevice as _TCA9548A
        # Valid ignore: get_i2c() returns either the real or compatibility I2C, both work at runtime
        return _TCA9548A(get_i2c(), self._address)  # ty: ignore[invalid-argument-type]

    def get_channel(self, number: int) -> Any:
        return self.device[number]


multiplexer_models: dict[str, type[Multiplexer]] = {
    hardware.__name__: hardware
    for hardware in [
        TCA9548A,
    ]
}
