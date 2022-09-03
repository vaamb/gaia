import io
import logging
import os
import pathlib
import typing as t
import weakref


from . import _IS_RASPI
from . multiplexers import get_i2c, get_multiplexer, TCA9548A
from ..utils import (
    pin_bcm_to_board, pin_board_to_bcm, pin_translation
)


if t.TYPE_CHECKING and 0:  # pragma: no cover
    from src.subroutines.template import SubroutineTemplate
    if _IS_RASPI:
        from adafruit_blinka import pwmio
        from adafruit_blinka.microcontroller.bcm283x.pin import Pin
    else:
        from ._compatibility import Pin, pwmio


sensorLogger = logging.getLogger("engine.hardware_lib")


def str_to_hex(address: str) -> int:
    if address.lower() in ("def", "default"):
        return 0
    return int(address, base=16)


class Address:
    __slots__ = ("type", "main", "multiplexer", "multiplexer_channel")

    def __init__(self, address_string: str):
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

    def __repr__(self):
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

    def _set_number(self, str_number: str):
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
    def is_multiplexed(self):
        return self.multiplexer != 0


class Hardware:
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
    def __init__(
            self,
            subroutine: "SubroutineTemplate",
            uid: str,
            address: str,
            level: str,
            type: str,
            model: str,
            **kwargs
    ) -> None:
        if subroutine == "hardware_creation":
            self._subroutine = None
        else:
            self._subroutine: "SubroutineTemplate" = weakref.proxy(subroutine)
        self._uid: str = uid
        if level.lower() in ("environment", "environments"):
            self._level: str = "environment"
        elif level.lower() in ("plant", "plants"):
            self._level: str = "plants"
        else:  # pragma: no cover
            raise ValueError("level should be 'plant' or 'environment'")
        self._type: str = type
        self._model: str = model
        self._name: str = kwargs.pop("name", self._uid)
        address_list: list = address.split(":")
        self._address: dict[str, Address] = {
            "main": Address(address_list[0])
        }
        try:
            self._address.update({"secondary": Address(address_list[1])})
        except IndexError:
            pass

    def __repr__(self):
        return f"<{self._uid} | {self._name} | {self._model}>"

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
    def subroutine(self) -> "SubroutineTemplate":
        return self._subroutine

    @property
    def address(self) -> dict[str, Address]:
        return self._address

    @property
    def address_repr(self):
        sec = self._address.get("secondary", None)
        if sec:
            return f"{self._address['main']}:{sec}"
        else:
            return str(self._address['main'])

    @property
    def model(self) -> str:
        return self._model

    @property
    def level(self) -> str:
        return self._level

    @property
    def type(self) -> str:
        return self._type

    @property
    def dict_repr(self) -> dict:
        return {
            "uid": self._uid,
            "name": self._name,
            "address": self.address_repr,
            "model": self._model,
            "type": self._type,
            "level": self._level,
        }


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
        self._pin = self._get_pin(self._address["main"].main)

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
            from ._compatibility import Pin
        return Pin(address)


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
        if "secondary" not in self._address:  # pragma: no cover
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
        if not self._address["secondary"].type in ("bcm", "board", "gpio"):  # pragma: no cover
            raise ValueError(
                "gpioDimmable address must be of type"
                "'addressType1_addressNum1:GPIO_pinNumber'"
            )
        self._PWMPin = self._get_pin(self._address["secondary"].main)
        self._dimmer = self._get_dimmer()

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
            from ._compatibility import pwmio
        return pwmio.PWMOut(self._PWMPin, frequency=100, duty_cycle=0)

    def set_pwm_level(self, duty_cycle_in_percent: t.Union[float, int]) -> None:
        duty_cycle_in_16_bit = duty_cycle_in_percent / 100 * (2**16 - 1)
        self._dimmer.duty_cycle = duty_cycle_in_16_bit


# TODO later: handle multiplex
class i2cHardware(Hardware):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not self._address["main"].type == "i2c":  # pragma: no cover
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
        kwargs["level"] = "plants"
        plants = kwargs.pop("plants", "")
        if not plants:  # pragma: no cover
            raise ValueError(
                "Plants-level hardware need to be provided a plant name "
                "as kwarg with the key name 'plant'"
            )
        self._plants = plants
        super().__init__(*args, **kwargs)

    @property
    def dict_repr(self) -> dict:
        base_repr = super().dict_repr
        base_repr["plant"] = self._plants
        return base_repr

    @property
    def plants(self) -> str:
        return self._plants


class BaseSensor(Hardware):
    def __init__(self, *args, **kwargs) -> None:
        kwargs["type"] = "sensor"
        super().__init__(*args, **kwargs)
        self._measure = kwargs.pop("measure", [])

    def get_data(self) -> list:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    @property
    def dict_repr(self) -> dict:
        base_repr = super().dict_repr
        base_repr["measure"] = self._measure
        return base_repr

    @property
    def measure(self) -> list:
        if isinstance(self._measure, str):
            return [self._measure]
        return self._measure

    @measure.setter
    def measure(self, new_measure: list) -> None:
        self._measure = new_measure


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
        kwargs["level"] = "environment"
        base_dir = self.subroutine.config.general.base_dir
        self.cam_dir = base_dir / f"camera/{self.subroutine.ecosystem_uid}"
        if not self.cam_dir.exists():
            os.mkdir(self.cam_dir)
        self.running = False
        super().__init__(*args, **kwargs)
        self.ecosystem_uid = self.subroutine.config.uid
        self._device = self._get_device()

    def _get_device(self):
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    def take_picture(self) -> pathlib.Path:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    def take_video(self) -> io.BytesIO:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )
