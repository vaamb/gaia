from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import textwrap
import typing as t
from typing import Any, Literal, Self
from weakref import WeakValueDictionary

from anyio.to_thread import run_sync

import gaia_validators as gv
from gaia_validators import safe_enum_from_name, safe_enum_from_value

from gaia.dependencies.camera import check_dependencies, cv2, SerializableImage
from gaia.hardware.multiplexers import Multiplexer, multiplexer_models
from gaia.hardware.utils import get_i2c, hardware_logger, is_raspi
from gaia.utils import (
    pin_bcm_to_board, pin_board_to_bcm, pin_translation)


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.subroutines.template import SubroutineTemplate

    if is_raspi():
        from adafruit_blinka.microcontroller.bcm283x.pin import Pin
    else:
        from gaia.hardware._compatibility import Pin


class Measure(Enum):
    absolute_humidity = "absolute_humidity"
    aqi = "AQI"
    capacitive = "capacitive"
    dew_point = "dew_point"
    eco2 = "eCO2"
    humidity = "humidity"
    light = "light"
    moisture = "moisture"
    temperature = "temperature"
    tvoc = "TVOC"
    # Camera
    mpri = "MPRI"
    ndrgi = "NDRGI"
    vari = "VARI"
    ndvi = "NDVI"


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
    PICAMERA = "PICAMERA"


def str_to_hex(address: str) -> int:
    if address.lower() in ("def", "default"):
        return 0
    return int(address, base=16)


class Address:
    __slots__ = ("type", "main", "multiplexer_address", "multiplexer_channel")

    type: AddressType
    main: int | None
    multiplexer_address: int | None
    multiplexer_channel: int | None

    def __init__(self, address_string: str) -> None:
        """
        :param address_string: properly written address. cf the _hint method
                               to see different address formats possible

        If any error arises while trying to create an Address, use `Address._hint()`
        """
        address_components = address_string.split("_", maxsplit=1)
        address_type = address_components[0]
        try:
            address_number = address_components[1]
        except IndexError:
            address_number = None

        # The hardware is using a standard GPIO pin
        if address_type.lower() in ("board", "bcm", "gpio"):
            # Get the pin number in the proper format and make sure it is valid
            pin_number = int(address_number)
            if address_type.lower() == "board":
                # Translate the pin number from "board" to "BCM" format
                if pin_number not in pin_board_to_bcm:  # pragma: no cover
                    raise PinNumberError("The pin is not a valid GPIO pin")
                pin_number = pin_translation(pin_number, "to_BCM")
            else:
                if pin_number not in pin_bcm_to_board:  # pragma: no cover
                    raise PinNumberError("The pin is not a valid GPIO pin")
            # Init the data
            self.type = AddressType.GPIO
            self.main = pin_number
            self.multiplexer_address = None  # No multiplexing possible with gpio
            self.multiplexer_channel = None  # No multiplexing possible with gpio

        # The hardware is using the I2C protocol
        elif address_type.lower() == "i2c":
            i2c_components = address_number.split("@")
            if len(i2c_components) == 1:
                # The hardware does not use a multiplexer; format "I2C_0x10"
                main = str_to_hex(i2c_components[0])
                multiplexer_address = None
                multiplexer_channel = None
            elif len(i2c_components) == 2:
                # The hardware is using a multiplexer; format "I2C_0x70#1_0x10"
                main = str_to_hex(i2c_components[1])
                multiplexer_components = i2c_components[0].split("#")
                multiplexer_address = str_to_hex(multiplexer_components[0])
                multiplexer_channel = int(multiplexer_components[1])
            else:
                raise ValueError(self._hint())
            self.type = AddressType.I2C
            self.main = main
            self.multiplexer_address = multiplexer_address
            self.multiplexer_channel = multiplexer_channel

        # The hardware is using the SPI protocol
        elif address_type.lower() == "spi":
            raise ValueError("SPI address is not currently supported.")

        elif address_type.lower() == "picamera":
            self.type = AddressType.PICAMERA
            self.main = None
            self.multiplexer_address = None
            self.multiplexer_channel = None

        # The address is not valid
        else:
            raise ValueError("Address type is not valid.")

    def __repr__(self) -> str:  # pragma: no cover
        if self.type == AddressType.PICAMERA:
            return f"{self.type.value}"
        elif self.type == AddressType.GPIO:
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
                f"{self.multiplexer_channel}@{rep_f(self.main)}"
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
            With a multiplexer: "I2C_0x70@1_0x10"
                    where "0x70" is the address of the multiplexer in hexadecimal,
                    "1" the channel used and "0x10" the address of the hardware
                    in hexadecimal
        SPI:
            Not implemented yet
        """
        return textwrap.dedent(msg)

    @property
    def is_multiplexed(self) -> bool:
        return self.multiplexer_address is not None


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
        "__weakref__",
        "_address_book",
        "_level",
        "_measures",
        "_model",
        "_multiplexer",
        "_name",
        "_plants",
        "_subroutine",
        "_type",
        "_uid",
    )

    def __init__(
            self,
            uid: str,
            name: str,
            address: str,
            level: gv.HardwareLevel,
            type: gv.HardwareType,
            model: str,
            measures: list[gv.Measure] | None = None,
            plants: list[str] or None = None,
            multiplexer_model: str | None = None,
            subroutine: SubroutineTemplate | None = None,
    ) -> None:
        self._subroutine: SubroutineTemplate | None
        if subroutine is None:
            self._subroutine = None
        else:
            self._subroutine = subroutine
        self._uid: str = uid
        self._name: str = name
        self._level: gv.HardwareLevel = level
        self._type: gv.HardwareType = type
        self._model: str = model
        self._name: str = name
        address_list: list = address.split("&")
        self._address_book: AddressBook = AddressBook(
            primary=Address(address_list[0]),
            secondary=Address(address_list[1]) if len(address_list) == 2 else None,
        )
        if multiplexer_model is None and self._address_book.primary.is_multiplexed:
            raise ValueError("Multiplexed address should be used with a multiplexer.")
        if (
            multiplexer_model is not None
            and not self._address_book.primary.is_multiplexed
        ):
            raise ValueError("Multiplexer can only be used with a multiplexed address.")
        if multiplexer_model:
            multiplexer_cls = multiplexer_models[multiplexer_model]
            self._multiplexer = multiplexer_cls(
                i2c_address=self._address_book.primary.multiplexer_address)
        else:
            self._multiplexer = None
        self._measures: dict[Measure, Unit | None] = self._format_measures(measures)
        self._plants = plants

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<{self.__class__.__name__}({self._uid}, name={self._name}, "
            f"model={self._model})>"
        )

    @classmethod
    def from_unclean(
            cls,
            subroutine: "SubroutineTemplate" | None,
            uid: str,
            address: str,
            level: gv.HardwareLevel | gv.HardwareLevelNames,
            type: gv.HardwareType | gv.HardwareTypeNames,
            model: str,
            name: str | None = None,
            measures: list[str] | None = None,
            plants: list[str] | None = None,
            multiplexer_model: str | None = None,
    ) -> Self:
        name: str = name or uid
        validated = gv.HardwareConfig(
            uid=uid,
            name=name,
            address=address,
            type=type,
            level=level,
            model=model,
            measures=measures,
            plants=plants,
            multiplexer_model=multiplexer_model,
        )
        return cls.from_hardware_config(validated, subroutine)

    @classmethod
    def from_hardware_config(
            cls,
            hardware_config: gv.HardwareConfig,
            subroutine: "SubroutineTemplate" | None,
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

    def _format_measures(
            self,
            measures: list[gv.Measure],
    ) -> dict[Measure, Unit | None]:
        rv: dict[Measure, Unit | None] = {}
        for m in measures:
            measure = safe_enum_from_name(Measure, m.name.lower())
            try:
                unit = safe_enum_from_value(Unit, m.unit)
            except ValueError:
                unit = None
            rv[measure] = unit
        return rv

    @classmethod
    def get_mounted(cls) -> dict[str, Self]:
        return _MetaHardware.instances

    @classmethod
    def get_mounted_by_uid(cls, uid: str) -> Self | None:
        return _MetaHardware.instances.get(uid)

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
            return f"{self._address_book.primary}&{self._address_book.secondary}"
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
    def multiplexer(self) -> Multiplexer | None:
        return self._multiplexer

    @property
    def multiplexer_model(self) -> str | None:
        if self._multiplexer:
            return self._multiplexer.__class__.__name__
        return None

    def dict_repr(self, shorten: bool = False) -> gv.HardwareConfigDict:
        base = gv.HardwareConfig(
            uid=self._uid,
            name=self._name,
            address=self.address_repr,
            type=self._type,
            level=self._level,
            model=self._model,
            measures=self._measures,
            plants=self._plants,
            multiplexer_model=self.multiplexer_model,
        ).model_dump(exclude_defaults=shorten)
        if base.get("measures"):
            base["measures"] = [
                f"{measure.value}|{unit.value if unit is not None else ''}"
                for measure, unit in self._measures.items()
            ]
        return base


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
        if is_raspi():  # pragma: no cover
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
    async def turn_on(self) -> None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover

    async def turn_off(self) -> None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover


class Dimmer(Hardware):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if self._address_book.secondary is None:  # pragma: no cover
            raise ValueError(
                "dimmable hardware address should be of form "
                "'addressType1_addressNum1&addressType2_addressNum2' with "
                "address 1 being for the main (on/off) switch and address 2 "
                "being PWM-able"
            )

    async def set_pwm_level(self, level) -> None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover


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
        if self.multiplexer is not None:
            address: Address = getattr(self._address_book, address_type)
            multiplexer_channel = address.multiplexer_channel
            return self.multiplexer.get_channel(multiplexer_channel)
        else:
            return get_i2c()


class PlantLevelHardware(Hardware):
    def __init__(self, *args, **kwargs) -> None:
        kwargs["level"] = gv.HardwareLevel.plants
        super().__init__(*args, **kwargs)
        if not self.plants:  # pragma: no cover
            raise ValueError(
                "Plants-level hardware should be provided a plant name "
                "as kwarg with the key name 'plants'."
            )


class BaseSensor(Hardware):
    measures_available: dict[Measure, Unit | None] | None = None

    def __init__(self, *args, **kwargs) -> None:
        if self.measures_available is None:
            raise NotImplementedError(
                "'cls.measures_available' should be a dict with 'measure: unit' "
                "as entries.")
        kwargs["type"] = gv.HardwareType.sensor
        super().__init__(*args, **kwargs)
        self._device: Any | None = None

    @property
    def device(self) -> Any:
        if self._device is None:
            self._device = self._get_device()
        return self._device

    def _get_device(self) -> Any:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover

    def _format_measures(
            self,
            measures: list[gv.Measure],
    ) -> dict[Measure, Unit | None]:
        formatted_measures: dict[Measure, Unit | None] = \
            super()._format_measures(measures)
        if not formatted_measures:
            formatted_measures = {
                measure: unit
                for measure, unit in self.measures_available.items()
            }
        else:
            err = ""
            validated: dict[Measure, Unit | None] = {}
            for measure, unit in self.measures_available.items():
                if measure not in self.measures_available:
                    err += (
                        f"Measure '{measure.name}' is not valid for sensor "
                        f"model '{self.model}'.\n"
                    )
                else:
                    validated[measure] = self.measures_available[measure]
            formatted_measures = validated
            if err:
                raise ValueError(err)
        return formatted_measures

    async def get_data(self) -> list[gv.SensorRecord]:
        raise NotImplementedError("This method must be implemented in a subclass")


class LightSensor(BaseSensor):
    async def get_lux(self) -> float | None:
        raise NotImplementedError("This method must be implemented in a subclass")

    async def get_data(self) -> list[gv.SensorRecord]:
        raise NotImplementedError("This method must be implemented in a subclass")


class gpioSensor(BaseSensor, gpioHardware):
    async def get_data(self) -> list[gv.SensorRecord]:
        raise NotImplementedError("This method must be implemented in a subclass")


class i2cSensor(BaseSensor, i2cHardware):
    def __init__(self, *args, default_address: int | None = None, **kwargs) -> None:
        if default_address is not None:
            address = kwargs["address"]
            if "def" in address:
                kwargs["address"] = f"I2C_{hex(default_address)}"
        super().__init__(*args, **kwargs)

    async def get_data(self) -> list[gv.SensorRecord]:
        raise NotImplementedError("This method must be implemented in a subclass")


class Camera(Hardware):
    def __init__(self, *args, **kwargs) -> None:
        check_dependencies()
        super().__init__(*args, **kwargs)
        self._device: Any | None = None
        self._camera_dir: Path | None = None

    @property
    def device(self) -> Any:
        if self._device is None:
            self._device = self._get_device()
        return self._device

    def _get_device(self) -> Any:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover

    async def get_image(self, size: tuple | None = None) -> SerializableImage:
        raise NotImplementedError("This method must be implemented in a subclass")

    #async def get_video(self) -> io.BytesIO:
    #    raise NotImplementedError(
    #        "This method must be implemented in a subclass"
    #    )

    @property
    def camera_dir(self) -> Path:
        if self._camera_dir is None:
            if self.subroutine is None:
                from gaia.config import GaiaConfigHelper

                config_cls = GaiaConfigHelper.get_config()
                base_dir = Path(config_cls.DIR)
                self._camera_dir = base_dir / "camera/orphan_camera"
            else:
                base_dir = self.subroutine.ecosystem.engine.config.base_dir
                self._camera_dir = base_dir / f"camera/{self.subroutine.ecosystem.name}"
            if not self._camera_dir.exists():
                self._camera_dir.mkdir(parents=True)
        return self._camera_dir

    async def load_image(self, image_path: Path) -> SerializableImage:
        image = await run_sync(SerializableImage.read, str(image_path))
        return image

    async def save_image(
            self,
            image: SerializableImage,
            image_path: Path | None = None,
    ) -> Path:
        if image_path is None:
            timestamp: datetime | None = image.metadata.get("timestamp", None)
            if timestamp is None:
                timestamp = datetime.now(tz=timezone.utc)
            image_path = f"{self.uid}-{timestamp.isoformat(timespec='seconds')}"
            image_path = self.camera_dir / image_path
        await run_sync(image.write, image_path)
        return image_path
