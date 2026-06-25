from __future__ import annotations

import typing as t
from types import EllipsisType
from typing import Any, ClassVar
from weakref import WeakValueDictionary

import busio  # TODO: maybe use the compatibility module ?

from gaia.hardware.utils import get_i2c, hardware_logger, is_raspi


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
    _requirements_error: ClassVar[Exception | None | EllipsisType] = ...

    def __init__(self, i2c_address: int, i2c: None | busio.I2C = None) -> None:
        if i2c is None:
            self._i2c = get_i2c()
        else:
            self._i2c = i2c
        self._address: int = i2c_address
        self.device = self._get_device()

    @classmethod
    async def _on_check_requirements(cls) -> None | Exception:
        """Override in subclasses for requirement checks logic."""
        pass

    @classmethod
    async def check_requirements(cls) -> None:
        if cls._requirements_error is Ellipsis:
            # The check hasn't been performed yet
            maybe_error = await cls._on_check_requirements()
            if isinstance(maybe_error, Exception):
                # Log the failed requirement
                hardware_logger.error(
                    f"Requirements not met for multiplexer {cls.__name__}. "
                    f"ERROR msg(s): `{maybe_error.__class__.__name__}: {maybe_error}`.")
            cls._requirements_error = maybe_error

        if cls._requirements_error is not None:
            # There was an error before, raise it
            raise cls._requirements_error

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
                    "Run `uv pip install adafruit-circuitpython-tca9548a` and "
                    "`uv pip install adafruit-circuitpython-busdevice` "
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
