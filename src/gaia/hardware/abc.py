from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import io
import os
from pathlib import Path
import re
import textwrap
import typing as t
from typing import Any, cast, Literal, Self
import weakref

from gaia_validators import (
    safe_enum_from_name, HardwareConfig, HardwareLevel, HardwareLevelNames,
    HardwareType, HardwareTypeNames, MeasureRecordDict)

from gaia.config import get_base_dir
from gaia.hardware.multiplexers import multiplexer_models
from gaia.hardware.utils import _IS_RASPI, get_i2c, hardware_logger
from gaia.utils import (
    pin_bcm_to_board, pin_board_to_bcm, pin_translation)


if t.TYPE_CHECKING:  # pragma: no cover
    import numpy as np

    from gaia.subroutines.template import SubroutineTemplate
    if _IS_RASPI:
        import pwmio
        from adafruit_blinka.microcontroller.bcm283x.pin import Pin
    else:
        from gaia.hardware._compatibility import Pin, pwmio


class PinNumberError(ValueError):
    pass


class AddressType(Enum):
    GPIO = "GPIO"
    I2C = "I2C"
    SPI = "SPI"


def str_to_hex(address: str) -> int:
    if address.lower() in ("def", "default"):
        return 0
    return int(address, base=16)


@dataclass(slots=True)
class Image:
    array: "np.array"
    timestamp: datetime


class Address:
    __slots__ = ("type", "main", "multiplexer_address", "multiplexer_channel")

    def __init__(self, address_string: str) -> None:
        """
        :param address_string: properly written address. cf the _hint method
                               to see different address formats possible
        """
        address_components = address_string.split("_")
        if len(address_components) != 2:
            raise ValueError(self._hint())

        address_data = self._extract_address_data(
            type_str=address_components[0],
            numbers_str=address_components[1]
        )
        self.type: AddressType = address_data[0]
        self.main: int = address_data[1]
        self.multiplexer_address: int = address_data[2]
        self.multiplexer_channel: int = address_data[3]

    def __repr__(self) -> str:
        if self.type == AddressType.GPIO:
            rep_f = int
        elif self.type == AddressType.I2C:
            rep_f = hex
        elif self.type == AddressType.SPI:
            rep_f = hex
        else:
            raise TypeError
        if self.multiplexer_address:
            return (
                f"{self.type.value}_{rep_f(self.multiplexer_address)}#"
                f"{self.multiplexer_channel}.{rep_f(self.main)}"
            )
        else:
            return f"{self.type.value}_{rep_f(self.main)}"

    @staticmethod
    def _hint() -> str:
        msg = """\
        Different types of address can be used: "GPIO" (using board or bcm
        numbers), "I2C" and "SPI" (currently not implemented).

        Here are some examples for the different address types:
        GPIO:
            Board numbers: "BOARD_37"
            BCM/GPIO numbers: "BCM_27"  == "GPIO_27"
        I2C:
            Without a multiplexer: "I2C_0x10"
            With a multiplexer: "I2C_0x70#1.0x10", where "0x70" is the
                                address of the multiplexer and "1" the
                                channel used
        SPI:
            Not implemented yet
        """
        return textwrap.dedent(msg)

    def _extract_address_data(
            self,
            type_str: str,
            numbers_str: str
    ) -> tuple[AddressType, int, int, int]:
        # Extract type
        address_type: AddressType
        if type_str.lower() in ("board", "bcm", "gpio"):
            address_type = AddressType.GPIO
        elif type_str.lower() == "i2c":
            address_type = AddressType.I2C
        elif type_str.lower() == "spi":
            address_type = AddressType.SPI
        else:
            raise ValueError("Address type is not supported")

        # Extract numbers
        main: int = 0
        multiplexer_address: int = 0
        multiplexer_channel: int = 0
        # GPIO-type address
        if address_type == AddressType.GPIO:
            try:
                number = int(numbers_str)
            except ValueError:
                raise ValueError(self._hint())
            if type_str.lower() == "board":
                if number not in pin_board_to_bcm:  # pragma: no cover
                    raise PinNumberError("The pin is not a valid GPIO pin")
                main = pin_translation(number, "to_BCM")
            else:
                if number not in pin_bcm_to_board:  # pragma: no cover
                    raise PinNumberError("The pin is not a valid GPIO pin")
                main = number
        # I2C type address
        elif address_type == AddressType.I2C:
            i2c_components = re.split("[#.]", numbers_str)
            if len(i2c_components) == 1:
                main = str_to_hex(i2c_components[0])
            elif len(i2c_components) == 3:
                multiplexer_address = str_to_hex(i2c_components[0])
                multiplexer_channel = str_to_hex(i2c_components[1])
                main = str_to_hex(i2c_components[2])
            else:
                raise ValueError(self._hint())
        return address_type, main, multiplexer_address, multiplexer_channel

    @property
    def is_multiplexed(self) -> bool:
        return self.multiplexer_address != 0


class _MetaHardware(type):
    instances: dict[str, Self] = {}

    def __call__(cls, *args, **kwargs):
        uid = kwargs["uid"]
        try:
            return cls.instances[uid]
        except KeyError:
            hardware = cls.__new__(cls, *args, **kwargs)
            hardware.__init__(*args, **kwargs)
            cls.instances[uid] = hardware
            return hardware


@dataclass(frozen=True, slots=True)
class AddressBook:
    primary: Address
    secondary: Address | None = None


AddressBookType = Literal["primary", "secondary"]


class Hardware(metaclass=_MetaHardware):
    """
    Base class for all hardware config creation and when creating hardware
    object from config file.
    A minimal hardware should have an uid (cf under), a name, an address,
    a model name, a type and a level.
    When creating a new hardware, use the
    specificConfig("your_environment").create_new_hardware() method. This will
    automatically generate a unique uid, properly format info and save it in
    ecosystems.cfg
    """
    __slots__ = (
        "_subroutine", "_uid", "_name", "_address_book", "_level", "_type",
        "_model", "_measures", "_multiplexer_model", "_plants"
    )

    def __init__(
            self,
            subroutine: "SubroutineTemplate" | None,
            uid: str,
            address: str,
            level: HardwareLevelNames,
            type: HardwareTypeNames,
            model: str,
            name: str | None = None,
            measures: list | None = None,
            plants: list or None = None,
            multiplexer_model: str | None = None,
    ) -> None:
        self._subroutine: "SubroutineTemplate" | None
        if subroutine is None:
            self._subroutine = None
        else:
            self._subroutine = weakref.proxy(subroutine)
        self._uid: str = uid
        self._level: HardwareLevel = cast(
            HardwareLevel, safe_enum_from_name(HardwareLevel, level))
        self._type: HardwareType = cast(
            HardwareType, safe_enum_from_name(HardwareType, type))
        self._model: str = model
        self._name: str = name or uid
        address_list: list = address.split(":")
        self._address_book: AddressBook = AddressBook(
            primary=Address(address_list[0]),
            secondary=Address(address_list[1]) if len(address_list) == 2 else None
        )
        if isinstance(measures, str):
            measures = [measures]
        self._measures = measures or []
        if isinstance(plants, str):
            plants = [plants]
        self._plants = plants or []
        self._multiplexer_model = multiplexer_model

    def __del__(self):
        # If an error arises during __init__ (because of a missing package), no
        #  instance will be registered
        if _MetaHardware.instances.get(self._uid):
            del _MetaHardware.instances[self._uid]

    def __repr__(self):
        return (
            f"<{self.__class__.__name__}({self._uid}, name={self._name}, "
            f"model={self._model})>"
        )

    @classmethod
    def get_actives_by_type(cls, type: HardwareType | str) -> dict[str, Self]:
        type = safe_enum_from_name(HardwareType, type)
        return {
            uid: hardware for uid, hardware in _MetaHardware.instances.items()
            if hardware.type is type
        }

    @classmethod
    def get_actives_by_level(cls, level: HardwareLevel) -> dict[str, Self]:
        level = safe_enum_from_name(HardwareLevel, level)
        return {
            uid: hardware for uid, hardware in _MetaHardware.instances.items()
            if hardware.level is level
        }

    @classmethod
    def from_hardware_config(
            cls,
            hardware_config: HardwareConfig,
            subroutine: "SubroutineTemplate" | None
    ) -> Self:
        return cls(
            subroutine=subroutine,
            uid=hardware_config.uid,
            name=hardware_config.name,
            address=hardware_config.address,
            level=hardware_config.level,
            type=hardware_config.type,
            model=hardware_config.model,
            measures=hardware_config.measures,
            plants=hardware_config.plants,
            multiplexer_model=hardware_config.multiplexer_model,
        )

    @property
    def subroutine(self) -> "SubroutineTemplate":
        return self._subroutine

    @property
    def uid(self) -> str:
        return self._uid

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, new_name: str) -> None:
        self._name = new_name

    @property
    def address_book(self) -> AddressBook:
        return self._address_book

    @property
    def address_repr(self) -> str:
        sec = self._address_book.secondary is not None
        if sec:
            return f"{self._address_book.primary}:{self._address_book.secondary}"
        else:
            return str(self._address_book.primary)

    @property
    def model(self) -> str:
        return self._model

    @property
    def level(self) -> HardwareLevel:
        return self._level

    @property
    def type(self) -> HardwareType:
        return self._type

    @property
    def measures(self) -> list[str]:
        return self._measures

    @property
    def plants(self) -> list[str]:
        return self._plants

    @property
    def multiplexer_model(self):
        return self._multiplexer_model

    def dict_repr(self, shorten: bool = False) -> dict:
        dict_repr = {
            "uid": self._uid,
            "name": self._name,
            "address": self.address_repr,
            "model": self._model,
            "type": self._type,
            "level": self._level,
        }
        if self._measures or not shorten:
            dict_repr["measures"] = self._measures
        if self._plants or not shorten:
            dict_repr["plants"] = self._plants
        return dict_repr


class gpioHardware(Hardware):
    IN = 0
    OUT = 1

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not self._address_book.primary.type == AddressType.GPIO:  # pragma: no cover
            raise ValueError(
                "gpioHardware address must be of type: 'GPIO_pinNumber', "
                "'BCM_pinNumber' or 'BOARD_pinNumber'"
            )

    def _get_pin(self, address) -> "Pin":
        if _IS_RASPI:
            try:
                from adafruit_blinka.microcontroller.bcm283x.pin import Pin
            except ImportError:
                raise RuntimeError(
                    "Adafruit blinka package is required. Run `pip install "
                    "adafruit-blinka` in your virtual env`."
                )
        else:
            from gaia.hardware._compatibility import Pin
        return Pin(address)

    @property
    def pin(self) -> "Pin":
        return self._get_pin(self._address_book.primary.main)


class Switch(Hardware):
    def __del__(self):
        try:
            self.turn_off()
        except AttributeError:  # Pin not yet setup
            pass

    def turn_on(self) -> None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover

    def turn_off(self) -> None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover


class Dimmer(Hardware):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self._address_book.secondary is None:  # pragma: no cover
            raise ValueError(
                "dimmable hardware address should be of form "
                "'addressType1_addressNum1:addressType2_addressNum2' with"
                "address 1 being for the main (on/off) switch and address 2 "
                "being PWM-able"
            )

    def __del__(self):
        self.set_pwm_level(0)

    def set_pwm_level(self, level) -> None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover


class gpioDimmer(gpioHardware, Dimmer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self._address_book.secondary.type == AddressType.GPIO:  # pragma: no cover
            raise ValueError(
                "gpioDimmable address must be of type"
                "'addressType1_addressNum1:GPIO_pinNumber'"
            )

    def _get_dimmer(self) -> "pwmio.PWMOut":
        if _IS_RASPI:
            try:
                import pwmio
            except ImportError:
                raise RuntimeError(
                    "Adafruit blinka package is required. Run `pip install "
                    "adafruit-blinka` in your virtual env`."
                )
        else:
            from gaia.hardware._compatibility import pwmio
        return pwmio.PWMOut(self.PWMPin, frequency=100, duty_cycle=0)

    def set_pwm_level(self, duty_cycle_in_percent: float | int) -> None:
        duty_cycle_in_16_bit = duty_cycle_in_percent / 100 * (2**16 - 1)
        self.dimmer.duty_cycle = duty_cycle_in_16_bit

    @property
    def PWMPin(self) -> "Pin":
        return self._get_pin(self._address_book.secondary.main)

    @property
    def dimmer(self) -> "pwmio.PWMOut":
        return self._get_dimmer()


class i2cHardware(Hardware):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not self._address_book.primary.type == AddressType.I2C:  # pragma: no cover
            raise ValueError(
                "i2cHardware address must be of type: 'I2C_default' or 'I2C_0' "
                "to use default sensor I2C address, or of type 'I2C_hexAddress' "
                "to use a specific address"
            )

    def _get_i2c(self, address: AddressBookType = "primary"):
        address: Address = getattr(self._address_book, address)
        if self._address_book[address].is_multiplexed:
            multiplexer_address = self._address_book[address].multiplexer_address
            multiplexer_channel = self._address_book[address].multiplexer_channel
            multiplexer_class = multiplexer_models[self.multiplexer_model]
            multiplexer = multiplexer_class(multiplexer_address)
            return multiplexer.get_channel(multiplexer_channel)
        else:
            return get_i2c()


class PlantLevelHardware(Hardware):
    def __init__(self, *args, **kwargs):
        kwargs["level"] = HardwareLevel.plants
        super().__init__(*args, **kwargs)
        if not self.plants:  # pragma: no cover
            hardware_logger.warning(
                "Plants-level hardware should be provided a plant name "
                "as kwarg with the key name 'plant'"
            )


class BaseSensor(Hardware):
    def __init__(self, *args, **kwargs) -> None:
        kwargs["type"] = HardwareType.sensor
        super().__init__(*args, **kwargs)
        self.device: Any = self._get_device()

    def _get_device(self) -> Any:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    def get_data(self) -> list[MeasureRecordDict]:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )


class LightSensor(BaseSensor):
    def get_lux(self) -> float:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    def get_data(self) -> list:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )


class gpioSensor(BaseSensor, gpioHardware):
    def get_data(self) -> list:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )


class i2cSensor(BaseSensor, i2cHardware):
    def __init__(self, *args, default_address: int | None = None, **kwargs) -> None:
        if default_address is not None:
            address = kwargs["address"]
            if "def" in address:
                kwargs["address"] = f"I2C_{hex(default_address)}"
        super().__init__(*args, **kwargs)

    def get_data(self) -> list:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )


class Camera(Hardware):
    def __init__(self, *args, **kwargs):
        import numpy as np
        from PIL import Image as _Image
        super().__init__(*args, **kwargs)
        self.device: Any = self._get_device()
        self._camera_dir: Path | None = None
        self.running: bool = False

    def _get_device(self) -> Any:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    def get_image(self) -> Image:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    def get_video(self) -> io.BytesIO:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    @property
    def camera_dir(self) -> Path:
        if self._camera_dir is None:
            base_dir = get_base_dir()
            self._camera_dir = base_dir/f"camera/{self.subroutine.ecosystem_uid}"
            if not self._camera_dir.exists():
                os.mkdir(self._camera_dir)
        return self._camera_dir

    def save_image(
            self,
            image: Image,
            name: str | None = None,
    ) -> Path:
        if name is None:
            name = f"{self.uid}-{image.timestamp.isoformat(timespec='seconds')}"
        path = self.camera_dir/name
        img = _Image.fromarray(image.array)
        img.save(path)
        return path
