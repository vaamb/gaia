from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import inspect
from pathlib import Path
import textwrap
from typing import Any, ClassVar, Literal, Self, Type, TYPE_CHECKING
from weakref import WeakValueDictionary

from anyio.to_thread import run_sync

import gaia_validators as gv
from gaia_validators import safe_enum_from_name, safe_enum_from_value

from gaia.dependencies.camera import check_dependencies, SerializableImage
from gaia.exceptions import HardwareNotFound
from gaia.hardware.multiplexers import Multiplexer, multiplexer_models
from gaia.hardware.utils import get_i2c, hardware_logger, is_raspi
from gaia.utils import pin_bcm_to_board, pin_board_to_bcm, pin_translation


if TYPE_CHECKING:  # pragma: no cover
    from gaia import Ecosystem

    if is_raspi():
        from adafruit_blinka.microcontroller.bcm283x.pin import Pin
        import busio
    else:
        from gaia.hardware._compatibility import busio, Pin


class InvalidAddressError(ValueError):
    """Raised when an invalid address is provided."""
    pass


class Measure(Enum):
    """Enum representing different types of measurements that can be taken by hardware."""
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
    # Camera-specific measures
    mpri = "MPRI"
    ndrgi = "NDRGI"
    vari = "VARI"
    ndvi = "NDVI"


class Unit(Enum):
    """Enum representing different units of measurement."""
    celsius_degree = "°C"
    lux = "lux"
    gram_per_cubic_m = "g.m-3"
    ppm = "ppm"
    rel_humidity = "% humidity"
    RWC = "RWC"


class AddressType(Enum):
    """Enum representing different types of hardware addresses."""
    GPIO = "GPIO"
    I2C = "I2C"
    SPI = "SPI"
    ONEWIRE = "ONEWIRE"
    PICAMERA = "PICAMERA"


def str_to_hex(address: str) -> int:
    if address.lower() in ("def", "default"):
        return 0
    return int(address, base=16)


def called_through(function: str) -> bool:
    stack = inspect.stack()
    for frame in stack:
        if frame.function == function:
            return True
    return False


# ---------------------------------------------------------------------------
#   Hardware address
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Address:
    """Represents a hardware address with support for different connection types.

    This class handles different types of hardware addresses including GPIO, I2C, SPI,
    and supports multiplexed connections.

    Attributes:
        type: The type of address (GPIO, I2C, SPI, PICAMERA).
        main: The main address or pin number.
        multiplexer_address: The address of the multiplexer if used.
        multiplexer_channel: The channel number on the multiplexer if used.
    """
    __slots__ = ("type", "main", "multiplexer_address", "multiplexer_channel")

    type: AddressType
    main: int | None
    multiplexer_address: int | None
    multiplexer_channel: int | None

    @classmethod
    def from_str(cls, address_string: str) -> Self:
        """Initialize an Address from a string representation.

        Args:
            address_string: String representation of the address.

        Raises:
            InvalidAddressError: For invalid GPIO pin numbers.

        Example:
            # GPIO address
            addr = Address("GPIO_17")

            # I2C address without multiplexer
            addr = Address("I2C_0x10")

            # I2C address with multiplexer
            addr = Address("I2C_0x70#1_0x10")
        """
        address_components = address_string.split("_", maxsplit=1)
        address_type = address_components[0].lower()
        try:
            address_number = address_components[1]
        except IndexError:
            address_number = None

        # The hardware is using a standard GPIO pin
        if address_type in {"board", "bcm", "gpio"}:
            # Get the pin number in the proper format and validate it
            try:
                pin_number = int(address_number)
            except ValueError as e:
                raise InvalidAddressError(f"Invalid pin number: {address_number}") from e

            if address_type == "board":
                # Translate the pin number from "board" to "BCM" format
                if pin_number not in pin_board_to_bcm:
                    raise InvalidAddressError(f"Board pin {pin_number} is not a valid GPIO pin")
                pin_number = pin_translation(pin_number, "to_BCM")
            else:
                if pin_number not in pin_bcm_to_board:
                    raise InvalidAddressError(f"BCM pin {pin_number} is not a valid GPIO pin")
            return cls(AddressType.GPIO, pin_number, None, None)

        # The hardware is using the I2C protocol
        elif address_type == "i2c":
            i2c_components = address_number.split("@")
            if len(i2c_components) == 1:
                # Format: "I2C_0x10", no multiplexer used
                try:
                    main = str_to_hex(i2c_components[0])
                except ValueError as e:
                    raise InvalidAddressError(f"Invalid I2C address: {i2c_components[0]}") from e
                multiplexer_address = None
                multiplexer_channel = None
            elif len(i2c_components) == 2:
                # Format: "I2C_0x70#1_0x10"
                try:
                    main = str_to_hex(i2c_components[1])
                    multiplexer_components = i2c_components[0].split("#")
                    if len(multiplexer_components) != 2:
                        raise ValueError
                    multiplexer_address = str_to_hex(multiplexer_components[0])
                    multiplexer_channel = int(multiplexer_components[1])
                except (ValueError, IndexError) as e:
                    raise InvalidAddressError(
                        "Invalid multiplexed I2C address format. Expected format: "
                        "'I2C_<multiplexer_addr>#<channel>@<device_addr>'"
                    ) from e
            else:
                raise InvalidAddressError(f"Invalid address type: {address_type}. {cls._hint()}")
            return cls(AddressType.I2C, main, multiplexer_address, multiplexer_channel)

        # The hardware is using the one wire protocol
        elif address_type == "onewire":
            main = address_number if address_number != "default" else None
            return cls(AddressType.ONEWIRE, main, None, None)

        # The hardware is a Pi Camera
        elif address_type.lower() == "picamera":
            return cls(AddressType.PICAMERA, None, None, None)

        # The hardware is using the SPI protocol
        elif address_type == "spi":
            raise NotImplementedError("SPI address type is not currently supported.")
        # The address is not valid
        else:
            raise InvalidAddressError(f"Invalid address type: {address_type}. {cls._hint()}")

    def __repr__(self) -> str:
        if self.type == AddressType.PICAMERA:
            return f"{self.type.value}"
        elif self.type == AddressType.ONEWIRE:
            return f"{self.type.value}_{self.main if self.main is not None else 'default'}"

        rep_f = hex if self.type in (AddressType.I2C, AddressType.SPI) else int

        if self.is_multiplexed:
            return (
                f"{self.type.value}_{rep_f(self.multiplexer_address)}#"
                f"{self.multiplexer_channel}@{rep_f(self.main)}"
            )
        return f"{self.type.value}_{rep_f(self.main)}"

    @staticmethod
    def _hint() -> str:
        """Provide usage hints for address formatting.

        Returns:
            str: Formatted help text explaining the address format options.
        """
        return textwrap.dedent("""
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
                With a multiplexer: "I2C_0x70#1@0x10"
                        where "0x70" is the address of the multiplexer in hexadecimal,
                        "1" the channel used and "0x10" the address of the hardware
                        in hexadecimal
                Alternatively, you can use the place holder "I2C_default" to use the 
                default I2C address of the hardware.
            
            1-Wire:
                "ONEWIRE_d1b4570a6461" where "d1b4570a6461" is the address of the 
                 hardware in hexadecimal.
                Alternatively, you can use the place holder "ONEWIRE_default" to 
                use the default 1-Wire address of the group of hardware.
            
            SPI:
                Not implemented yet
        """)

    @property
    def is_multiplexed(self) -> bool:
        """Check if this address uses a multiplexer.

        Returns:
            bool: True if the address uses a multiplexer, False otherwise.
        """
        return (
            self.multiplexer_address is not None
            and self.multiplexer_channel is not None
        )


# ---------------------------------------------------------------------------
#   Base Hardware
# ---------------------------------------------------------------------------
class _MetaHardware(type):
    instances: WeakValueDictionary[str, Hardware] = WeakValueDictionary()

    def __call__(cls, *args, **kwargs) -> Hardware:
        uid = kwargs["uid"]
        try:
            return cls.instances[uid]
        except KeyError:
            hardware = cls.__new__(cls, *args, **kwargs)
            hardware.__init__(*args, **kwargs)
            if hardware.ecosystem is not None:
                cls.instances[uid] = hardware
            return hardware


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
        "_active",
        "_address",
        "_ecosystem",
        "_groups",
        "_level",
        "_measures",
        "_model",
        "_multiplexer",
        "_name",
        "_plants",
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
            *,
            groups: set[str] | list[str] | None = None,
            measures: list[gv.Measure] | None = None,
            plants: list[str] | None = None,
            active: bool = True,
            multiplexer_model: str | None = None,
            ecosystem: Ecosystem | None = None,
    ) -> None:
        if ecosystem is None:
            # ecosystem can be `None` ONLY when `Hardware` is called through `validate_hardware_dict`
            if not called_through("validate_hardware_dict"):
                raise RuntimeError("ecosystem can be set to `None` only during hardware validation")
        self._ecosystem: Ecosystem | None = ecosystem
        self._uid: str = uid
        self._name: str = name
        self._active: bool = active
        self._level: gv.HardwareLevel = level
        self._type: gv.HardwareType = type
        self._groups: set[str] = set(groups) if groups else set()
        self._model: str = model
        self._address = Address.from_str(address)
        if multiplexer_model is None and self._address.is_multiplexed:
            raise ValueError("Multiplexed address should be used with a multiplexer.")
        if multiplexer_model is not None and not self._address.is_multiplexed:
            raise ValueError("Multiplexer can only be used with a multiplexed address.")
        if multiplexer_model:
            multiplexer_cls = multiplexer_models[multiplexer_model]
            self._multiplexer = multiplexer_cls(
                i2c_address=self._address.multiplexer_address)
        else:
            self._multiplexer = None
        measures = measures or []
        self._measures: dict[Measure, Unit | None] = self._format_measures(measures)
        self._plants = plants or []

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<{self.__class__.__name__}({self._uid}, name={self._name}, "
            f"model={self._model})>"
        )

    @classmethod
    def _unsafe_from_config(
            cls,
            hardware_config: gv.HardwareConfig,
            ecosystem: Ecosystem | None,
    ) -> Self:
        # Should only be used directly for validation
        return cls(
            ecosystem=ecosystem,
            uid=hardware_config.uid,
            name=hardware_config.name,
            active=hardware_config.active,
            address=hardware_config.address,
            level=hardware_config.level,
            type=hardware_config.type,
            groups=hardware_config.groups,
            model=hardware_config.model,
            measures=hardware_config.measures,
            plants=hardware_config.plants,
            multiplexer_model=hardware_config.multiplexer_model,
        )

    async def _on_initialize(self) -> None:
        """Override in subclasses for initialization logic."""
        pass

    @classmethod
    async def initialize(
            cls,
            hardware_cfg: gv.HardwareConfig,
            ecosystem: Ecosystem,
    ) -> Self:
        if hardware_cfg.uid in _MetaHardware.instances:
            raise RuntimeError(f"Hardware {hardware_cfg.uid} already exists.")
        # Ensure a virtual hardware will be return if virtualization is enabled
        if (
                ecosystem.engine.config.app_config.VIRTUALIZATION
                and hardware_cfg.type & gv.HardwareType.sensor
        ):
            if not hardware_cfg.model.startswith("virtual"):
                hardware_cfg.model = f"virtual{hardware_cfg.model}"
        # Get the subclass needed based on the model used
        hardware_cls = cls.get_model_subclass(hardware_cfg.model)
        # Create hardware
        hardware = hardware_cls._unsafe_from_config(hardware_cfg, ecosystem)
        # Perform subclass-specific initialization routine
        await hardware._on_initialize()
        return hardware

    async def _on_terminate(self) -> None:
        """Override in subclasses for termination logic."""
        pass

    async def terminate(self) -> None:
        # Perform subclass-specific termination routine
        await self._on_terminate()
        # Reset actuator handlers using this hardware
        for actuator_handler in self.ecosystem.actuator_hub.actuator_handlers.values():
            if self in actuator_handler.get_linked_actuators():
                actuator_handler.reset_cached_actuators()

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

    @classmethod
    def detach_instance(cls, uid: str) -> None:
        _MetaHardware.instances.pop(uid)

    @property
    def ecosystem(self) -> Ecosystem | None:
        return self._ecosystem

    @property
    def ecosystem_uid(self) -> str | None:
        if self._ecosystem is None:
            return None
        return self._ecosystem.uid

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
    def active(self) -> bool:
        return self._active

    @active.setter
    def active(self, new_active: bool) -> None:
        self._active = new_active

    @property
    def address(self) -> Address:
        return self._address

    @property
    def address_repr(self) -> str:
        return str(self._address)

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
    def groups(self) -> set[str]:
        if "__type__" in self._groups:
            return self._groups - {"__type__"} | {self._type.name}
        return self._groups

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

    @classmethod
    def get_model_subclass(cls, model: str) -> Type[Hardware]:
        from gaia.hardware import hardware_models

        try:
            return hardware_models[model]
        except KeyError:
            raise HardwareNotFound(f"{model} is not implemented.")

    def dict_repr(self, shorten: bool = False) -> gv.HardwareConfigDict:
        return gv.HardwareConfig(
            uid=self._uid,
            name=self._name,
            address=self.address_repr,
            type=self._type,
            level=self._level,
            groups=self._groups,
            model=self._model,
            measures=self._measures,
            plants=self._plants,
            multiplexer_model=self.multiplexer_model,
        ).model_dump(exclude_defaults=shorten)


# ---------------------------------------------------------------------------
#   Subclasses based on address type
# ---------------------------------------------------------------------------
class gpioHardware(Hardware):
    #__slots__ = ("_pin",)  # Find a way around the multiple inheritance issue

    IN = 0
    OUT = 1

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not self._address.type == AddressType.GPIO:  # pragma: no cover
            raise ValueError(
                "gpioHardware address must be of type: 'GPIO_pinNumber', "
                "'BCM_pinNumber' or 'BOARD_pinNumber'"
            )
        self._pin: Pin | None = None

    @property
    def pin(self) -> Pin:
        if self._pin is None:
            self._pin = self._get_pin()
        return self._pin

    def _get_pin(self) -> Pin:
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
        address = self.address.main
        return Pin(address)


class i2cHardware(Hardware):
    __slots__ = ()

    default_address: ClassVar[int | None] = None

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not self._address.type == AddressType.I2C:  # pragma: no cover
            raise ValueError(
                "i2cHardware address must be of type: 'I2C_default' or 'I2C_0' "
                "to use default sensor I2C address, or of type 'I2C_hexAddress' "
                "to use a specific address"
            )

        def inject_default_address(address: Address) -> Address:
            # Using default address if address is 0
            main = address.main
            multiplexer_address = address.multiplexer_address
            if address.main == 0x0:
                main = self.default_address
            if address.is_multiplexed:
                if address.multiplexer_address == 0x0:
                    multiplexer_address = self.multiplexer.address
            return Address(address.type, main, multiplexer_address, address.multiplexer_channel)

        self._address = inject_default_address(self._address)

    def _get_i2c(self) -> busio.I2C:
        if self.multiplexer is not None:
            multiplexer_channel = self.address.multiplexer_channel
            return self.multiplexer.get_channel(multiplexer_channel)
        else:
            return get_i2c()


class OneWireHardware(Hardware):
    __slots__ = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.address.type == AddressType.ONEWIRE:  # pragma: no cover
            raise ValueError(
                "OneWireHardware address must be of type: 'ONEWIRE_hexAddress' "
                "to use a specific address or 'ONEWIRE_default' to use the default "
                "address"
            )
        # Check that 1-wire is enabled
        # TODO: move this in an async initialize fct
        if is_raspi():
            import subprocess
            lsmod = subprocess.Popen("lsmod", stdout=subprocess.PIPE)
            grep = subprocess.Popen(("grep", "-i", "w1_"), stdin=lsmod.stdout)
            return_code = grep.wait()
            if return_code != 0:
                raise RuntimeError(
                    "1-wire is not enabled. Run `sudo raspi-config` and enable 1-wire."
                )

    @property
    def device_address(self) -> str | None:
        return self.address.main


class Camera(Hardware):
    __slots__ = ("_device", "_camera_dir")

    def __init__(self, *args, **kwargs) -> None:
        check_dependencies()
        super().__init__(*args, **kwargs)
        if not self.address.type == AddressType.PICAMERA:  # pragma: no cover
            raise ValueError("Camera address must be 'PICAMERA'")
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
            if self.ecosystem is None:
                from gaia.config import GaiaConfigHelper

                config_cls = GaiaConfigHelper.get_config()
                base_dir = Path(config_cls.DIR)
                self._camera_dir = base_dir / "camera/orphan_camera"
            else:
                base_dir = self.ecosystem.engine.config.base_dir
                self._camera_dir = base_dir / f"camera/{self.ecosystem.name}"
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


# ---------------------------------------------------------------------------
#   Subclasses based on hardware type/function
# ---------------------------------------------------------------------------
class Actuator(Hardware):
    __slots__ = ()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if self.type not in gv.HardwareType.actuator:
            raise ValueError("Type should be in `HardwareType.actuator`")


class Switch(Actuator):
    __slots__ = ()

    async def _on_initialize(self) -> None:
        await super()._on_initialize()
        success = await self.turn_off()
        if not success:
            hardware_logger.warning(
                f"Failed to turn {self.name} ({self.uid}) off")

    async def _on_terminate(self) -> None:
        await super()._on_terminate()
        success = await self.turn_off()
        if not success:
            hardware_logger.warning(
                f"Failed to turn {self.name} ({self.uid}) off")

    async def turn_on(self) -> bool:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover

    async def turn_off(self) -> bool:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover


class Dimmer(Actuator):
    __slots__ = ()

    async def _on_initialize(self) -> None:
        await super()._on_initialize()
        success = await self.set_pwm_level(0)
        if not success:
            hardware_logger.warning(
                f"Failed to set {self.name} ({self.uid})'s PWM level to 0")

    async def _on_terminate(self) -> None:
        await super()._on_terminate()
        success = await self.set_pwm_level(0)
        if not success:
            hardware_logger.warning(
                f"Failed to set {self.name} ({self.uid})'s PWM level to 0")

    async def set_pwm_level(self, level: float | int) -> bool:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover


class BaseSensor(Hardware):
    __slots__ = ("_device",)

    measures_available: ClassVar[dict[Measure, Unit | None] | Ellipsis | None] = None

    def __init__(self, *args, **kwargs) -> None:
        if self.measures_available is None:
            raise NotImplementedError(
                "'cls.measures_available' should either be a dict with "
                "´measure: unit´ as entries, or ´...´ as value."
            )
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

        # If no measure is specified ...
        if not formatted_measures:
            # ... make sure we have some default measures available ...
            if self.measures_available is Ellipsis:
                raise ValueError(
                    f"Measures must be specified for sensor model '{self.model}'."
                )
            # ... and return them
            return self.measures_available

        # If we don't have any default measures available don't perform any check
        if self.measures_available is Ellipsis:
            return formatted_measures

        # Otherwise, validate the measures
        err = ""
        for measure in formatted_measures:
            if measure not in self.measures_available:
                err += (
                    f"Measure '{measure.name}' is not valid for sensor "
                    f"model '{self.model}'.\n"
                )
        if err:
            raise ValueError(err)
        # Assign the unit from the default measures available
        return {
            measure: self.measures_available[measure]
            for measure in formatted_measures
        }

    async def get_data(self) -> list[gv.SensorRecord]:
        raise NotImplementedError("This method must be implemented in a subclass")


class LightSensor(BaseSensor):
    __slots__ = ()

    async def get_lux(self) -> float | None:
        raise NotImplementedError("This method must be implemented in a subclass")

    async def get_data(self) -> list[gv.SensorRecord]:
        raise NotImplementedError("This method must be implemented in a subclass")


# ---------------------------------------------------------------------------
#   Other simple subclasses
# ---------------------------------------------------------------------------
class PlantLevelHardware(Hardware):
    __slots__ = ()

    def __init__(self, *args, **kwargs) -> None:
        kwargs["level"] = gv.HardwareLevel.plants
        super().__init__(*args, **kwargs)
        if not self.plants:  # pragma: no cover
            raise ValueError(
                "Plants-level hardware should be provided a plant name "
                "as kwarg with the key name 'plants'."
            )


# ---------------------------------------------------------------------------
#   Composition subclasses
# ---------------------------------------------------------------------------
class gpioSensor(BaseSensor, gpioHardware):
    __slots__ = ("_device", "_pin")

    async def get_data(self) -> list[gv.SensorRecord]:
        raise NotImplementedError("This method must be implemented in a subclass")


class i2cSensor(BaseSensor, i2cHardware):
    __slots__ = ()

    async def get_data(self) -> list[gv.SensorRecord]:
        raise NotImplementedError("This method must be implemented in a subclass")
