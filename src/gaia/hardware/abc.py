from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import io
import logging
import os
from pathlib import Path
import typing as t
from typing import cast, Self
import weakref

import numpy as np
from PIL import Image as _Image

from gaia_validators import (
    safe_enum_from_name, HardwareConfig, HardwareLevel, HardwareLevelNames,
    HardwareType, HardwareTypeNames
)

from gaia.config import get_base_dir
from gaia.hardware import _IS_RASPI
from gaia.hardware.multiplexers import get_i2c, get_multiplexer
from gaia.utils import (
    pin_bcm_to_board, pin_board_to_bcm, pin_translation
)


if t.TYPE_CHECKING and 0:  # pragma: no cover
    from gaia.subroutines.template import SubroutineTemplate
    if _IS_RASPI:
        from adafruit_blinka import pwmio
        from adafruit_blinka.microcontroller.bcm283x.pin import Pin
    else:
        from gaia.hardware._compatibility import Pin, pwmio


hardware_logger = logging.getLogger("engine.hardware_lib")


def str_to_hex(address: str) -> int:
    if address.lower() in ("def", "default"):
        return 0
    return int(address, base=16)


@dataclass
class Image:
    array: np.array
    timestamp: datetime


class Address:
    __slots__ = ("type", "main", "multiplexer", "multiplexer_channel")

    def __init__(self, address_string: str) -> None:
        """
        :param address_string: str: address in form 'GPIO_1'
        """
        address_components = address_string.split("_")
        if len(address_components) != 2:
            raise ValueError
        self.type: str = address_components[0].lower()
        self.main: int = 0
        self.multiplexer: int = 0
        self.multiplexer_channel: int = 0
        self._set_number(address_components[1])

    def __repr__(self) -> str:
        if self.type == "i2c":
            rep_f = hex
        else:
            rep_f = int
        if self.multiplexer:
            return (
                f"{self.type.upper()}_{rep_f(self.multiplexer)}#"
                f"{self.multiplexer_channel}.{rep_f(self.main)}"
            )
        else:
            return f"{self.type.upper()}_{rep_f(self.main)}"

    def _set_number(self, str_number: str) -> None:
        if self.type.lower() in ("board", "bcm", "gpio"):
            number = int(str_number)
            if self.type.lower() == "board":
                if number not in pin_board_to_bcm:  # pragma: no cover
                    raise ValueError("The pin is not a valid GPIO pin")
                self.main = pin_translation(number, "to_BCM")
            else:
                if number not in pin_bcm_to_board:  # pragma: no cover
                    raise ValueError("The pin is not a valid GPIO pin")
                self.main = number
        elif self.type.lower() == "i2c":
            i2c_components = str_number.split(".")
            if len(i2c_components) > 1:
                self.main = str_to_hex(i2c_components[1])
                multiplexer_components = i2c_components[0].split("#")
                self.multiplexer = str_to_hex(multiplexer_components[0])
                self.multiplexer_channel = str_to_hex(multiplexer_components[1])
            else:
                self.main = str_to_hex(i2c_components[0])

    @property
    def is_multiplexed(self) -> bool:
        return self.multiplexer != 0


class _MetaHardware(type):
    instances: dict[str, Self] = {}

    def __call__(cls, *args, **kwargs):
        uid = kwargs.get("uid")
        if uid not in cls.instances and uid is not None:
            cls.instances[uid] = cls.__new__(cls, *args, **kwargs)
            cls.instances[uid].__init__(*args, **kwargs)
        return cls.instances[uid]


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
        "_subroutine", "_uid", "_name", "_address", "_level", "_type",
        "_model", "_measures", "_plants"
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
        self._address: dict[str, Address] = {"main": Address(address_list[0])}
        if len(address_list) == 2:
            self._address.update({"secondary": Address(address_list[1])})
        measures = measures or []
        if isinstance(measures, str):
            measures = [measures]
        self._measures = measures
        plants = plants or []
        if isinstance(plants, str):
            plants = [plants]
        self._plants = plants

    def __del__(self):
        del _MetaHardware.instances[self._uid]

    def __repr__(self):
        return (
            f"<{self.__class__.__name__}({self._uid}, name={self._name}, "
            f"model={self._model})>"
        )

    @classmethod
    def get_actives_by_type(cls, type: HardwareType | str):
        type = safe_enum_from_name(HardwareType, type)
        return {
            uid: hardware for uid, hardware in _MetaHardware.instances.items()
            if hardware.type is type
        }

    @classmethod
    def get_actives_by_level(cls, level: HardwareLevel):
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
    ):
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
    def address(self) -> dict[str, Address]:
        return self._address

    @property
    def address_repr(self) -> str:
        sec = self._address.get("secondary", None)
        if sec:
            return f"{self._address['main']}:{sec}"
        else:
            return str(self._address['main'])

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
    def measures(self) -> list:
        return self._measures

    @property
    def plants(self) -> list:
        return self._plants

    @property
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
        if not self._address["main"].type in ("bcm", "board", "gpio"):  # pragma: no cover
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
        return self._get_pin(self._address["main"].main)


class Switch(Hardware):
    def __del__(self):
        self.turn_off()

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
        if "secondary" not in self.address:  # pragma: no cover
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
        if not self.address["secondary"].type in ("bcm", "board", "gpio"):  # pragma: no cover
            raise ValueError(
                "gpioDimmable address must be of type"
                "'addressType1_addressNum1:GPIO_pinNumber'"
            )

    def _get_dimmer(self) -> "pwmio.PWMOut":
        if _IS_RASPI:
            try:
                from adafruit_blinka import pwmio
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
        return self._get_pin(self._address["secondary"].main)

    @property
    def dimmer(self) -> "pwmio.PWMOut":
        return self._get_dimmer()


# TODO later: handle multiplex
class i2cHardware(Hardware):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not self.address["main"].type == "i2c":  # pragma: no cover
            raise ValueError(
                "i2cHardware address must be of type: 'I2C_default' or 'I2C_0' "
                "to use default sensor I2C address, or of type 'I2C_hexAddress' "
                "to use a specific address"
            )

    def _get_i2c(self, address: str = "main"):
        if self.address[address].is_multiplexed:
            multiplexer_address = self.address[address].multiplexer
            multiplexer_channel = self.address[address].multiplexer_channel
            multiplexer = get_multiplexer(multiplexer_address)
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

    def get_data(self) -> list:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )


class LightSensor(BaseSensor):
    def _get_lux(self) -> float:
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
    def get_data(self) -> list:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )


class Camera(Hardware):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._camera_dir: Path | None = None
        self.running: bool = False

    def _get_device(self):
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

    @property
    def device(self):
        return self._get_device()

    def get_image(self) -> Image:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

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

    def get_video(self) -> io.BytesIO:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )
