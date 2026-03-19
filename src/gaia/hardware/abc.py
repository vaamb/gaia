from __future__ import annotations

from abc import ABC, ABCMeta, abstractmethod
import asyncio
from asyncio import  Event, Future, sleep, Task
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import inspect
from logging import getLogger, Logger
from pathlib import Path
import textwrap
from types import EllipsisType
import typing as t
from typing import Any, ClassVar, NamedTuple, Self, Type
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

from anyio.to_thread import run_sync
from pydantic import ValidationError
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK

import gaia_validators as gv
from gaia_validators import safe_enum_from_name, safe_enum_from_value

from gaia.dependencies.camera import check_dependencies, SerializableImage
from gaia.exceptions import DeviceError, HardwareNotFound
from gaia.hardware._websocket import WebSocketHardwareManager
from gaia.hardware.multiplexers import Multiplexer, multiplexer_models
from gaia.hardware.utils import get_i2c, hardware_logger, is_raspi
from gaia.utils import pin_bcm_to_board, pin_board_to_bcm, pin_translation


if t.TYPE_CHECKING:  # pragma: no cover
    from websockets import ServerConnection

    from gaia import Ecosystem

    if is_raspi():
        from adafruit_blinka.microcontroller.bcm283x.pin import Pin
        import busio
    else:
        from gaia.hardware._compatibility import busio, Pin


class InvalidAddressError(ValueError):
    """Raised when an invalid address is provided."""
    pass


# ---------------------------------------------------------------------------
#   Enums
# ---------------------------------------------------------------------------
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


class SensorRead(NamedTuple):
    sensor_uid: str
    measure: str
    value: float | None


# ---------------------------------------------------------------------------
#   Utility functions
# ---------------------------------------------------------------------------
def ip_is_valid(address: str) -> bool:
    import re

    ip_regex: str = r"(?:([0-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-5])(\.(?!$)|$)){4}"
    return bool(re.match(ip_regex, address))


def str_to_hex(address: str) -> int:
    if address.lower() in ("def", "default"):
        return 0
    return int(address, base=16)


# ---------------------------------------------------------------------------
#   Validation models
# ---------------------------------------------------------------------------
class WebSocketMessage(gv.BaseModel):
    uuid: UUID | None = None
    data: Any


# ---------------------------------------------------------------------------
#   Hardware address
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Address(ABC):
    """Represents a hardware address with support for different connection types.

    This class handles different types of hardware addresses including GPIO, I2C, SPI,
    and supports multiplexed connections.

    Attributes:
        main: The main address or pin number.
        multiplexer_address: The address of the multiplexer if used.
        multiplexer_channel: The channel number on the multiplexer if used.
    """
    __slots__ = ("main", "multiplexer_address", "multiplexer_channel")

    main: int | str | None
    multiplexer_address: int | None
    multiplexer_channel: int | None

    @staticmethod
    def from_str(address_string: str) -> Address:
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
            # These hardware need to have an address specified
            assert address_number is not None
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
            return GPIOAddress(pin_number, None, None)

        # The hardware is using the I2C protocol
        elif address_type == "i2c":
            # These hardware need to have a str address specified
            assert isinstance(address_number, str)
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
                raise InvalidAddressError(f"Invalid address type: {address_type}. {Address._hint()}")
            return I2CAddress(main, multiplexer_address, multiplexer_channel)

        # The hardware is using the one wire protocol
        elif address_type == "onewire":
            main = address_number if address_number != "default" else None
            return OneWireAddress(main, None, None)

        # The hardware is a Pi Camera
        elif address_type.lower() == "picamera":
            return PiCameraAddress(None, None, None)

        # The hardware is using WebSockets
        elif address_type.lower() == "websocket":
            if address_number is not None and not ip_is_valid(address_number):
                raise InvalidAddressError(
                    "Invalid websocket address format. Expected format: "
                    "'WEBSOCKET' or 'WEBSOCKET_<remote_ip_addr>'"
                )
            return WebSocketAddress(address_number, None, None)

        # The hardware is using the SPI protocol
        elif address_type == "spi":
            raise NotImplementedError("SPI address type is not currently supported.")
        # The address is not valid
        else:
            raise InvalidAddressError(f"Invalid address type: {address_type}. {Address._hint()}")

    @abstractmethod
    def __repr__(self) -> str:
        ...

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


@dataclass(frozen=True)
class GPIOAddress(Address):
    main: int
    multiplexer_address: None
    multiplexer_channel: None

    def __repr__(self) -> str:
        return f"GPIO_{self.main}"


@dataclass(frozen=True)
class I2CAddress(Address):
    main: int
    multiplexer_address: int | None
    multiplexer_channel: int | None

    def __repr__(self) -> str:
        if self.is_multiplexed:
            # For type narrowing, it is tested in `is_multiplexed`
            assert self.multiplexer_address is not None
            return (
                f"I2C_{hex(self.multiplexer_address)}#"
                f"{self.multiplexer_channel}@{hex(self.main)}"
            )
        return f"I2C_{hex(self.main)}"


@dataclass(frozen=True)
class SPIAddress(Address):
    main: int
    multiplexer_address: int | None
    multiplexer_channel: int | None

    def __repr__(self) -> str:
        return f"SPI_{hex(self.main)}"


@dataclass(frozen=True)
class OneWireAddress(Address):
    main: str | None
    multiplexer_address: None
    multiplexer_channel: None

    def __repr__(self) -> str:
        return f"ONEWIRE_{self.main if self.main is not None else 'default'}"


@dataclass(frozen=True)
class PiCameraAddress(Address):
    main: None
    multiplexer_address: None
    multiplexer_channel: None

    def __repr__(self) -> str:
        return "PICAMERA"


@dataclass(frozen=True)
class WebSocketAddress(Address):
    main: str | None
    multiplexer_address: None
    multiplexer_channel: None

    def __repr__(self) -> str:
        return f"WEBSOCKET_{self.main}" if self.main else "WEBSOCKET"


# ---------------------------------------------------------------------------
#   Base Hardware
# ---------------------------------------------------------------------------
class _MetaHardware(ABCMeta):
    instances: WeakValueDictionary[str, Hardware] = WeakValueDictionary()

    def __call__(cls, *args, **kwargs) -> Hardware:
        uid = kwargs["uid"]
        try:
            return cls.instances[uid]
        except KeyError:
            # Valid ignore: cls.__new__ in metaclass __call__ always returns an
            #  instance of the concrete subclass
            hardware: Hardware = cls.__new__(cls, *args, **kwargs)  # ty: ignore[invalid-assignment]
            hardware.__init__(*args, **kwargs)
            if kwargs.get("ecosystem"):
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
        "_logger",
        "_measures",
        "_model",
        "_multiplexer",
        "_name",
        "_plants",
        "_type",
        "_uid",
    )

    @classmethod
    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if inspect.isabstract(cls):
            return  # abstract classes don't need a hardware mixin yet
        address_mixins = {
            base for base in cls.__mro__
            if HardwareAddressMixin in getattr(base, "__bases__", ())
        }
        if len(address_mixins) != 1:
            raise TypeError(
                f"{cls.__name__} must include exactly one HardwareAddressMixin "
                f"subclass in its MRO, found {len(address_mixins)}: "
                f"{[b.__name__ for b in address_mixins]}"
            )

    def __init__(
            self,
            ecosystem: Ecosystem,
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
    ) -> None:
        self._logger: Logger = getLogger(f"gaia.hardware.{uid}")
        self._ecosystem: Ecosystem = ecosystem
        self._uid: str = uid
        self._name: str = name
        self._active: bool = active
        self._level: gv.HardwareLevel = level
        self._type: gv.HardwareType = type
        self._groups: set[str] = set(groups) if groups else set()
        self._model: str = model
        self._address = self.validate_address(address)
        if multiplexer_model is None and self._address.is_multiplexed:
            raise ValueError("Multiplexed address should be used with a multiplexer.")
        if multiplexer_model is not None and not self._address.is_multiplexed:
            raise ValueError("Multiplexer can only be used with a multiplexed address.")
        if multiplexer_model:
            # For type narrowing, if `multiplexer_model` is set, so is `address.multiplexer_address`
            assert self._address.multiplexer_address is not None
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
    def from_config(
            cls,
            hardware_config: gv.HardwareConfig,
            ecosystem: Ecosystem,
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

    @classmethod
    @abstractmethod
    def validate_address(cls, address_str: str) -> Address:
        ...

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
        hardware = hardware_cls.from_config(hardware_cfg, ecosystem)
        # Perform subclass-specific initialization routine
        await hardware._on_initialize()
        # Valid ignore: hardware_cls is a subclass of cls, so `_unsafe_from_config`
        #  returns a compatible Self
        return hardware  # ty: ignore[invalid-return-type]

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
    def get_mounted(cls) -> WeakValueDictionary[str, Self]:
        # Valid ignore: instances stores all Hardware subclasses; Self narrowing is caller's responsibility
        return _MetaHardware.instances  # ty: ignore[invalid-return-type]

    @classmethod
    def get_mounted_by_uid(cls, uid: str) -> Self | None:
        # Valid ignore: instances stores all Hardware subclasses; Self narrowing is caller's responsibility
        return _MetaHardware.instances.get(uid)  # ty: ignore[invalid-return-type]

    @classmethod
    def detach_instance(cls, uid: str) -> None:
        _MetaHardware.instances.pop(uid)

    @property
    def ecosystem(self) -> Ecosystem:
        return self._ecosystem

    @property
    def ecosystem_uid(self) -> str | None:
        return self.ecosystem.uid

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
        # Valid ignores: implicit conversion done by pydantic
        return gv.HardwareConfig(
            uid=self._uid,
            name=self._name,
            address=self.address_repr,
            type=self._type,
            level=self._level,
            groups=self._groups,  # ty: ignore[invalid-argument-type]
            model=self._model,
            measures=self._measures,  # ty: ignore[invalid-argument-type]
            plants=self._plants,
            multiplexer_model=self.multiplexer_model,
        ).model_dump(exclude_defaults=shorten)


# ---------------------------------------------------------------------------
#   Mixins for each address type
# ---------------------------------------------------------------------------
class HardwareAddressMixin:
    """Marker base for hardware address-protocol mixins.
    """
    __slots__ = ()


class gpioAddressMixin(HardwareAddressMixin):
    """Protocol mixin for GPIO-addressed hardware. Expects `self.address: GPIOAddress`."""

    IN = 0
    OUT = 1

    if t.TYPE_CHECKING:
        address: GPIOAddress

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pin: Pin | None = None

    @classmethod
    def validate_address(cls, address_str: str) -> GPIOAddress:
        address = Address.from_str(address_str)
        if not isinstance(address, GPIOAddress):  # pragma: no cover
            raise ValueError(
                "GPIO hardware address must be of type: 'GPIO_pinNumber', "
                "'BCM_pinNumber' or 'BOARD_pinNumber'"
            )
        return address

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
        # GPIO hardware should have int addresses
        assert isinstance(address, int)
        return Pin(address)


class i2cAddressMixin(HardwareAddressMixin):
    """Protocol mixin for I2C-addressed hardware. Expects `self.address: I2CAddress`."""
    __slots__ = ()

    default_address: ClassVar[int | None] = None

    if t.TYPE_CHECKING:
        address: I2CAddress
        multiplexer: Multiplexer | None

    @classmethod
    def validate_address(cls, address_str: str) -> I2CAddress:
        address = Address.from_str(address_str)
        if not isinstance(address, I2CAddress):  # pragma: no cover
            raise ValueError(
                "I2C hardware address must be of type: 'I2C_default' or 'I2C_0' "
                "to use default sensor I2C address, or of type 'I2C_hexAddress' "
                "to use a specific address"
            )
        # Use the hardware ´default_address´ if the address provided in the config is 0x0 (== "default")
        if address.main != 0x0:
            return address
        if cls.default_address is None:
            raise ValueError("Cannot use a default address with this hardware.")
        return I2CAddress(
            cls.default_address, address.multiplexer_address, address.multiplexer_channel)

    def _get_i2c(self) -> busio.I2C:
        if self.multiplexer is not None:
            multiplexer_channel = self.address.multiplexer_channel
            # For type narrowing, it is set at the same time as `multiplexer`
            assert multiplexer_channel is not None
            return self.multiplexer.get_channel(multiplexer_channel)
        else:
            return get_i2c()


class OneWireAddressMixin(HardwareAddressMixin):
    """Protocol mixin for 1-Wire-addressed hardware. Expects `self.address: OneWireAddress`."""
    __slots__ = ()

    if t.TYPE_CHECKING:
        address: OneWireAddress

    @classmethod
    def validate_address(cls, address_str: str) -> OneWireAddress:
        address = Address.from_str(address_str)
        if not isinstance(address, OneWireAddress):  # pragma: no cover
            raise ValueError(
                "OneWire hardware address must be of type: 'ONEWIRE_hexAddress' "
                "to use a specific address or 'ONEWIRE_default' to use the default "
                "address"
            )
        return address

    async def _on_initialize(self) -> None:
        await super()._on_initialize()  # ty: ignore[unresolved-attribute]

        def check_1w_enabled() -> None:
            import subprocess
            lsmod = subprocess.Popen("lsmod", stdout=subprocess.PIPE)
            grep = subprocess.Popen(("grep", "-i", "w1_"), stdin=lsmod.stdout)
            return_code = grep.wait()
            if return_code != 0:
                raise RuntimeError(
                    "1-wire is not enabled. Run `sudo raspi-config` and enable 1-wire."
                )

        if is_raspi():
            await run_sync(check_1w_enabled)

    @property
    def device_address(self) -> str | None:
        assert not isinstance(self.address.main, int)
        return self.address.main


class PiCameraAddressMixin(HardwareAddressMixin):
    """Protocol mixin for PiCamera hardware."""
    __slots__ = ()

    if t.TYPE_CHECKING:
        address: PiCameraAddress

    @classmethod
    def validate_address(cls, address_str: str) -> PiCameraAddress:
        address = Address.from_str(address_str)
        if not isinstance(address, PiCameraAddress):  # pragma: no cover
            raise ValueError("PiCamera address must be 'PICAMERA'")
        return address


class WebSocketAddressMixin(HardwareAddressMixin):
    """Protocol mixin for WebSocket-addressed hardware. Expects Hardware attributes."""
    _websocket_manager: WebSocketHardwareManager | None = None

    if t.TYPE_CHECKING:
        uid: str
        address: WebSocketAddress
        ecosystem: Ecosystem
        _logger: Logger

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if WebSocketAddressMixin._websocket_manager is None:
            manager = WebSocketHardwareManager(self.ecosystem.engine.config)
            WebSocketAddressMixin._websocket_manager = manager
        self._websocket_manager = WebSocketAddressMixin._websocket_manager
        self._requests: dict[UUID, Future] = {}
        self._task: Task | None = None
        self._stop_event: Event = Event()

    @classmethod
    def validate_address(cls, address_str: str) -> WebSocketAddress:
        address = Address.from_str(address_str)
        if not isinstance(address, WebSocketAddress):  # pragma: no cover
            raise ValueError(
                "WebSocket hardware address must be of type: 'WEBSOCKET' or "
                "'WEBSOCKET_<remote_ip_addr>'"
            )
        return address

    async def _on_initialize(self) -> None:
        await super()._on_initialize()  # ty: ignore[unresolved-attribute]
        await self.register()

    async def _on_terminate(self) -> None:
        await super()._on_terminate()  # ty: ignore[unresolved-attribute]
        await self.unregister()

    @property
    def connected(self) -> bool:
        return self.websocket_manager.get_connection(self.uid) is not None

    @property
    def websocket_manager(self) -> WebSocketHardwareManager:
        if self._websocket_manager is None:
            raise RuntimeError("WebsocketManager not initialized")
        return self._websocket_manager

    async def _connection_loop(self) -> None:
        wait_time: int = 1
        while not self._stop_event.is_set():
            connection = self.websocket_manager.get_connection(self.uid)
            if connection is not None:
                wait_time = 1
                try:
                    await self._listening_loop(connection)
                except ConnectionClosed:
                    # The connection closed, try to reconnect
                    pass
            try:
                await asyncio.wait_for(self._stop_event.wait(), wait_time)
            except TimeoutError:
                wait_time *= 2
                wait_time = min(32, wait_time)

    async def _listening_loop(self, connection: ServerConnection) -> None:
        async for msg in connection:
            try:
                parsed_msg = WebSocketMessage.model_validate_json(msg)
                # `WebSocketAddressMixin` work on the master-slave model and should
                #  never receive an unsolicited message (with no request UUID)
                #  once the device has registered
                assert parsed_msg.uuid is not None
                uuid: UUID = parsed_msg.uuid
                data: Any = parsed_msg.data
                if uuid not in self._requests:
                    self._logger.error(
                        f"Received a message with an unknown uuid {uuid}: {data}")
                    continue
                self._requests[uuid].set_result(data)
            except ValidationError:
                self._logger.error(
                    f"Encountered an error while parsing the message {msg}")
                continue

    async def _send_msg_and_forget(self, msg: Any) -> None:
        connection = self.websocket_manager.get_connection(self.uid)
        if connection is None:
            raise ConnectionError(f"Hardware '{self.uid}' is not registered.")
        payload = WebSocketMessage(uuid=None, data=msg).model_dump_json()
        await connection.send(payload)

    async def _send_msg_and_wait(self, msg: Any, timeout: int | float = 60) -> Any:
        connection = self.websocket_manager.get_connection(self.uid)
        if connection is None:
            raise ConnectionError(f"Hardware '{self.uid}' is not registered.")
        uuid: UUID = uuid4()
        self._requests[uuid] = Future()
        payload = WebSocketMessage(uuid=uuid, data=msg).model_dump_json()
        await connection.send(payload)
        try:
            return await asyncio.wait_for(self._requests[uuid], timeout)
        except TimeoutError:
            self._logger.error(f"Timeout while waiting for response from device '{self.uid}'")
            raise
        finally:
            self._requests.pop(uuid, None)

    async def _execute_action(self, action: dict, error_msg: str) -> Any:
        try:
            response = await self._send_msg_and_wait(action)
        except (ConnectionError, ConnectionClosedOK, TimeoutError) as e:
            self._logger.error(f"Could not connect: {e}")
            raise ConnectionError() from e
        if response.get("status") != "success":
            msg = response.get("message", "")
            self._logger.error(f"{error_msg}. {msg}" if msg else error_msg)
            raise DeviceError(msg)
        return response["data"]

    async def register(self) -> None:
        if not self.websocket_manager.is_running:
            await self.websocket_manager.start()
        assert not isinstance(self.address.main, int)
        await self.websocket_manager.register_hardware(self.uid, self.address.main)
        self._task = asyncio.create_task(self._connection_loop())
        await sleep(0)  # Allow the task to start

    async def unregister(self) -> None:
        try:
            await self._send_msg_and_forget("disconnecting")
        except (ConnectionError, ConnectionClosedOK):
            # The device is not connected
            pass
        await self.websocket_manager.unregister_hardware(self.uid)
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            await sleep(0)  # Allow the task to be canceled
            self._task = None
        # If not more hardware are registered, there is no need to keep the manager
        #  running
        if (
                self.websocket_manager.is_running
                and self.websocket_manager.registered_hardware == 0
        ):
            await self.websocket_manager.stop()


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

    async def get_status(self) -> bool:
        raise NotImplementedError("This method must be implemented in a subclass")


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

    async def get_pwm_level(self) -> float:
        raise NotImplementedError("This method must be implemented in a subclass")


class BaseSensor(Hardware):
    __slots__ = ("_device",)

    measures_available: ClassVar[dict[Measure, Unit | None] | EllipsisType | None] = None

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
            assert isinstance(self.measures_available, dict)
            return self.measures_available

        # If we don't have any default measures available don't perform any check
        if self.measures_available is Ellipsis:
            return formatted_measures

        # Otherwise, validate the measures
        assert isinstance(self.measures_available, dict)
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

    async def get_data(self) -> list[SensorRead]:
        raise NotImplementedError("This method must be implemented in a subclass")


class LightSensor(BaseSensor):
    __slots__ = ()

    async def get_lux(self) -> float | None:
        raise NotImplementedError("This method must be implemented in a subclass")

    async def get_data(self) -> list[SensorRead]:
        raise NotImplementedError("This method must be implemented in a subclass")


class Camera(Hardware):
    __slots__ = ("_device", "_camera_dir")

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
            base_dir = self.ecosystem.engine.config.gaia_dir
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
            file_name = f"{self.uid}-{timestamp.isoformat(timespec='seconds')}"
            image_path = self.camera_dir / file_name
        await run_sync(image.write, image_path)
        return image_path


# ---------------------------------------------------------------------------
#   Other simple subclasses
# ---------------------------------------------------------------------------
class PlantLevelMixin:
    __slots__ = ()

    if t.TYPE_CHECKING:
        plants: list[str]

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
class gpioSensor(gpioAddressMixin, BaseSensor):
    __slots__ = ()

    async def get_data(self) -> list[SensorRead]:
        raise NotImplementedError("This method must be implemented in a subclass")


class i2cSensor(i2cAddressMixin, BaseSensor):
    __slots__ = ()

    async def get_data(self) -> list[SensorRead]:
        raise NotImplementedError("This method must be implemented in a subclass")
