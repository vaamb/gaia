from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import enum
from enum import Enum
import io
import os
from pathlib import Path
import textwrap
import typing as t
from typing import Any, Literal, Self, Type
import weakref
from weakref import WeakValueDictionary

import gaia_validators as gv
from gaia_validators import safe_enum_from_name, safe_enum_from_value

from gaia.dependencies.camera import check_dependencies, Image
from gaia.hardware.multiplexers import Multiplexer, multiplexer_models
from gaia.hardware.utils import get_i2c, hardware_logger, is_raspi
from gaia.utils import (
    pin_bcm_to_board, pin_board_to_bcm, pin_translation)


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.subroutines.template import SubroutineTemplate

    if is_raspi():
        import pwmio
        from adafruit_blinka.microcontroller.bcm283x.pin import Pin
    else:
        from gaia.hardware._compatibility import Pin, pwmio


class Measure(Enum):
    absolute_humidity = enum.auto()
    AQI = enum.auto()
    capacitive = enum.auto()
    dew_point = enum.auto()
    eCO2 = enum.auto()
    humidity = enum.auto()
    light = enum.auto()
    moisture = enum.auto()
    temperature = enum.auto()
    TVOC = enum.auto()


class Unit(Enum):
    celsius_degree = "°C"
    lux = "lux"
    gram_per_cubic_m = "g.m-3"
    ppm = "ppm"
    rel_humidity = "% humidity"
    RWC = "RWC"


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


class Address:
    __slots__ = ("type", "main", "multiplexer_address", "multiplexer_channel")

    def __init__(self, address_string: str) -> None:
        """
        :param address_string: properly written address. cf the _hint method
                               to see different address formats possible
        """
        address_components = address_string.split("_", maxsplit=2)
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
                    where "37" is the pin number using the board notation
            BCM/GPIO numbers: "BCM_27"  == "GPIO_27"
                    where "32" is the pin number using the gpio notation
        I2C:
            Without a multiplexer: "I2C_0x10"
                    where "0x10" is the address of the hardware in hexadecimal
            With a multiplexer: "I2C_0x70#1_0x10"
                    where "0x70" is the address of the multiplexer in hexadecimal,
                    "1" the channel used and "0x10" the address of the hardware
                    in hexadecimal
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
            i2c_components = numbers_str.split("_")
            try:
                if len(i2c_components) == 1:
                    main = str_to_hex(i2c_components[0])
                elif len(i2c_components) == 2:
                    multiplexer_components = i2c_components[0].split("#")
                    multiplexer_address = str_to_hex(multiplexer_components[0])
                    multiplexer_channel = int(multiplexer_components[1])
                    main = str_to_hex(i2c_components[1])
                else:
                    raise ValueError
            except ValueError:
                raise ValueError(self._hint())
        elif address_type == AddressType.SPI:
            raise ValueError("SPI address is not currently supported.")
        return address_type, main, multiplexer_address, multiplexer_channel

    @property
    def is_multiplexed(self) -> bool:
        return self.multiplexer_address != 0


class _MetaHardware(type):
    instances: WeakValueDictionary[str, "Hardware"] = WeakValueDictionary()

    def __call__(cls, *args, **kwargs) -> "Hardware":
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
        "__weakref__", "_subroutine", "_uid", "_name", "_address_book",
        "_level", "_type", "_model", "_measures", "_multiplexer_model", "_plants"
    )

    def __init__(
            self,
            subroutine: "SubroutineTemplate" | None,
            uid: str,
            address: str,
            level: gv.HardwareLevel | gv.HardwareLevelNames,
            type: gv.HardwareType | gv.HardwareTypeNames,
            model: str,
            name: str | None = None,
            measures: list[str] | None = None,
            plants: list[str] or None = None,
            multiplexer_model: str | None = None,
    ) -> None:
        self._subroutine: "SubroutineTemplate" | None
        if subroutine is None:
            self._subroutine = None
        else:
            self._subroutine = weakref.proxy(subroutine)
        self._uid: str = uid
        self._level: gv.HardwareLevel = safe_enum_from_name(gv.HardwareLevel, level)
        self._type: gv.HardwareType = safe_enum_from_name(gv.HardwareType, type)
        self._model: str = model
        self._name: str = name or uid
        address_list: list = address.split(":")
        self._address_book: AddressBook = AddressBook(
            primary=Address(address_list[0]),
            secondary=Address(address_list[1]) if len(address_list) == 2 else None
        )
        self._measures: dict[Measure, Unit | None] = self._validate_measures(measures)
        if isinstance(plants, str):
            plants = [plants]
        self._plants = plants or []
        self._multiplexer_model = multiplexer_model

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}({self._uid}, name={self._name}, "
            f"model={self._model})>"
        )

    @staticmethod
    def _validate_measures(
            measures: list[str] | None
    ) -> dict[Measure, Unit | None]:
        if measures is None:
            measures = []
        elif isinstance(measures, str):
            measures = [measures]
        rv: dict[Measure, Unit | None] = {}
        for m in measures:
            measure_and_unit = m.split("|")
            measure = safe_enum_from_name(Measure, measure_and_unit[0])
            try:
                raw_unit = measure_and_unit[1]
            except IndexError:
                unit = None
            else:
                unit = safe_enum_from_value(Unit, raw_unit)
            rv[measure] = unit
        return rv

    @classmethod
    def get_mounted(cls) -> dict[str, Self]:
        return _MetaHardware.instances

    @classmethod
    def get_mounted_by_uid(cls, uid: str) -> Self | None:
        return _MetaHardware.instances.get(uid)

    @classmethod
    def from_hardware_config(
            cls,
            hardware_config: gv.HardwareConfig,
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
    def subroutine(self) -> "SubroutineTemplate" | None:
        return self._subroutine

    @property
    def ecosystem_uid(self) -> str | None:
        if self._subroutine is None:
            return None
        return self._subroutine.ecosystem.uid

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
    def level(self) -> gv.HardwareLevel:
        return self._level

    @property
    def type(self) -> gv.HardwareType:
        return self._type

    @property
    def measures(self) -> dict[Measure, Unit | None]:
        return self._measures

    @property
    def plants(self) -> list[str]:
        return self._plants

    @property
    def multiplexer_model(self):
        return self._multiplexer_model

    def dict_repr(self, shorten: bool = False) -> gv.HardwareConfigDict:
        model = gv.HardwareConfig(
            uid=self._uid,
            name=self._name,
            address=self.address_repr,
            type=self._type,
            level=self._level,
            model=self._model,
            measures=[
                f"{measure.name}|{unit.value}"
                for measure, unit in self._measures.items()
            ],
            plants=self._plants,
            multiplexer_model=self._multiplexer_model,
        )
        return model.model_dump(exclude_defaults=shorten)


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
        self._pin: "Pin" | None = None

    @staticmethod
    def _get_pin(address) -> "Pin":
        if is_raspi():
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
        if self._pin is None:
            self._pin = self._get_pin(self._address_book.primary.main)
        return self._pin


class Switch(Hardware):
    def turn_on(self) -> None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover

    def turn_off(self) -> None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover


class Dimmer(Hardware):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if self._address_book.secondary is None:  # pragma: no cover
            raise ValueError(
                "dimmable hardware address should be of form "
                "'addressType1_addressNum1:addressType2_addressNum2' with"
                "address 1 being for the main (on/off) switch and address 2 "
                "being PWM-able"
            )

    def set_pwm_level(self, level) -> None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover


class gpioDimmer(gpioHardware, Dimmer):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not self._address_book.secondary.type == AddressType.GPIO:  # pragma: no cover
            raise ValueError(
                "gpioDimmable address must be of type"
                "'addressType1_addressNum1:GPIO_pinNumber'"
            )
        self._pwm_pin: "Pin" | None = None
        self._dimmer : "pwmio.PWMOut" | None = None

    def _get_dimmer(self) -> "pwmio.PWMOut":
        if is_raspi():
            try:
                import pwmio
            except ImportError:
                raise RuntimeError(
                    "Adafruit blinka package is required. Run `pip install "
                    "adafruit-blinka` in your virtual env`."
                )
        else:
            from gaia.hardware._compatibility import pwmio
        return pwmio.PWMOut(self.pwm_pin, frequency=100, duty_cycle=0)

    def set_pwm_level(self, duty_cycle_in_percent: float | int) -> None:
        duty_cycle_in_16_bit = duty_cycle_in_percent / 100 * (2**16 - 1)
        self.dimmer.duty_cycle = duty_cycle_in_16_bit

    @property
    def pwm_pin(self) -> "Pin":
        if not self._pwm_pin:
            self._pwm_pin = self._get_pin(self._address_book.secondary.main)
        return self._pwm_pin

    @property
    def dimmer(self) -> "pwmio.PWMOut":
        if not self._dimmer:
            self._dimmer = self._get_dimmer()
        return self._dimmer


class i2cHardware(Hardware):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not self._address_book.primary.type == AddressType.I2C:  # pragma: no cover
            raise ValueError(
                "i2cHardware address must be of type: 'I2C_default' or 'I2C_0' "
                "to use default sensor I2C address, or of type 'I2C_hexAddress' "
                "to use a specific address"
            )

    def _get_i2c(self, address_type: AddressBookType = "primary"):
        address: Address = getattr(self._address_book, address_type)
        if address.is_multiplexed:
            multiplexer_address = address.multiplexer_address
            multiplexer_channel = address.multiplexer_channel
            multiplexer_class: Type[Multiplexer] = \
                multiplexer_models[self.multiplexer_model]
            multiplexer: Multiplexer = multiplexer_class(multiplexer_address)
            return multiplexer.get_channel(multiplexer_channel)
        else:
            return get_i2c()


class PlantLevelHardware(Hardware):
    def __init__(self, *args, **kwargs) -> None:
        kwargs["level"] = gv.HardwareLevel.plants
        super().__init__(*args, **kwargs)
        if not self.plants:  # pragma: no cover
            hardware_logger.warning(
                "Plants-level hardware should be provided a plant name "
                "as kwarg with the key name 'plants'"
            )


class BaseSensor(Hardware):
    measures_available: dict[Measure, Unit | None] | None = None

    def __init__(self, *args, **kwargs) -> None:
        if self.measures_available is None:
            raise NotImplementedError(
                f"'cls.measures_available' should be a dict with 'measure: unit' "
                f"as entries.")
        kwargs["type"] = gv.HardwareType.sensor
        measures = kwargs.get("measures")
        validated_measures: list[str]
        if not measures:
            validated_measures = [
                f"{measure.name}|{unit.value}"
                for measure, unit in self.measures_available.items()
            ]
        else:
            measures: list[str]
            validated_measures = []
            err = ""
            for measure_and_unit in measures:
                measure = measure_and_unit.split("|")[0]
                try:
                    m = Measure[measure.lower()]
                    if m not in self.measures_available.keys():
                        raise KeyError  # Ugly but works
                except KeyError:
                    model = kwargs["model"]
                    err += f"Measure '{measure}' is not valid for sensor " \
                           f"model '{model}'.\n"
                else:
                    unit: Unit | None = self.measures_available[m]
                    validated_measures.append(
                        f"{m.name}|{unit.value if unit is not None else ''}"
                    )
            if err:
                raise ValueError(err)
        kwargs["measures"] = validated_measures
        super().__init__(*args, **kwargs)
        self.device: Any = self._get_device()

    def _get_device(self) -> Any:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    def get_data(self) -> list[gv.SensorRecord]:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )


class LightSensor(BaseSensor):
    def get_lux(self) -> float | None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    def get_data(self) -> list[gv.SensorRecord]:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )


class gpioSensor(BaseSensor, gpioHardware):
    def get_data(self) -> list[gv.SensorRecord]:
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

    def get_data(self) -> list[gv.SensorRecord]:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )


class Camera(Hardware):
    def __init__(self, *args, **kwargs) -> None:
        check_dependencies()
        super().__init__(*args, **kwargs)
        self.device: Any = self._get_device()
        self._camera_dir: Path | None = None
        self.running: bool = False

    def _get_device(self) -> Any:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    def get_image(self) -> Image | None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    def get_video(self) -> io.BytesIO | None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    @property
    def camera_dir(self) -> Path:
        if self._camera_dir is None:
            if self.subroutine is None:
                base_dir = Path(os.getcwd())
            else:
                base_dir = self.subroutine.ecosystem.engine.config.base_dir
            self._camera_dir = base_dir/f"camera/{self.subroutine.ecosystem.name}"
            if not self._camera_dir.exists():
                os.mkdir(self._camera_dir)
        return self._camera_dir

    def save_image(
            self,
            image: Image,
            name: str | None = None,
    ) -> Path:
        if name is None:
            timestamp: datetime | None = image.metadata.get("timestamp")
            if timestamp is None:
                timestamp = datetime.now(tz=timezone.utc)
            name = f"{self.uid}-{timestamp.isoformat(timespec='seconds')}"
        path = self.camera_dir/name
        image.save(path)
        return path
