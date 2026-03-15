from __future__ import annotations

import typing as t
from typing import Type

from gaia.hardware.abc import BaseSensor
from gaia.hardware.sensors.GPIO import DHTSensor
from gaia.hardware.sensors.I2C import (
    AHT20, CapacitiveMoisture, ENS160, VCNL4040, VEML7700)
from gaia.hardware.sensors.websocket import WebSocketSensor
from gaia.hardware.utils import hardware_logger
from gaia.hardware.virtual import virtualHardware


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.hardware.sensors._devices._compatibility import (
        AHTx0Device,
        DHT11Device,
        DHT22Device,
        ENS160Device,
        SeesawDevice,
        VCNL4040Device,
        VEML7700Device,
    )


# Valid ignore: __slots__ layout conflict is a known CPython limitation with multiple inheritance; works at runtime
class virtualSensor(virtualHardware, BaseSensor):  # ty: ignore[instance-layout-conflict]
    __slots__ = ()


class virtualDHT(DHTSensor, virtualSensor):
    __slots__ = ()


class virtualDHT11(virtualDHT):
    __slots__ = ()

    def _get_device(self) -> DHT11Device:
        from gaia.hardware.sensors._devices._compatibility import DHT11Device

        if not self.ecosystem:
            hardware_logger.warning(
                f"'{self}' did not receive any ecosystem, Virtualization disabled.")
        return DHT11Device(ecosystem=self.ecosystem)


class virtualDHT22(virtualDHT):
    __slots__ = ()

    def _get_device(self) -> DHT22Device:
        from gaia.hardware.sensors._devices._compatibility import DHT22Device

        if not self.ecosystem:
            hardware_logger.warning(
                f"'{self}' did not receive any ecosystem, Virtualization disabled.")
        return DHT22Device(ecosystem=self.ecosystem)


class virtualAHT20(AHT20, virtualSensor):
    __slots__ = ()

    def _get_device(self) -> AHTx0Device:
        from gaia.hardware.sensors._devices._compatibility import AHTx0Device

        if not self.ecosystem:
            hardware_logger.warning(
                f"'{self}' did not receive any ecosystem, Virtualization disabled.")
        return AHTx0Device(ecosystem=self.ecosystem)


class virtualVCNL4040(VCNL4040, virtualSensor):
    __slots__ = ()

    def _get_device(self) -> VCNL4040Device:
        from gaia.hardware.sensors._devices._compatibility import VCNL4040Device

        if not self.ecosystem:
            hardware_logger.warning(
                f"'{self}' did not receive any ecosystem, Virtualization disabled.")
        return VCNL4040Device(ecosystem=self.ecosystem)


class virtualVEML7700(VEML7700, virtualSensor):
    __slots__ = ()

    def _get_device(self) -> VEML7700Device:
        from gaia.hardware.sensors._devices._compatibility import VEML7700Device

        if not self.ecosystem:
            hardware_logger.warning(
                f"'{self}' did not receive any ecosystem, Virtualization disabled.")
        return VEML7700Device(ecosystem=self.ecosystem)


class virtualCapacitiveMoisture(CapacitiveMoisture, virtualSensor):
    __slots__ = ()

    def _get_device(self) -> SeesawDevice:
        from gaia.hardware.sensors._devices._compatibility import SeesawDevice

        if not self.ecosystem:
            hardware_logger.warning(
                f"'{self}' did not receive any ecosystem, Virtualization disabled.")
        return SeesawDevice(ecosystem=self.ecosystem)


class virtualENS160(ENS160, virtualSensor):
    __slots__ = ()

    def _get_device(self) -> ENS160Device:
        from gaia.hardware.sensors._devices._compatibility import ENS160Device

        if not self.ecosystem:
            hardware_logger.warning(
                f"'{self}' did not receive any ecosystem, Virtualization disabled.")
        return ENS160Device(ecosystem=self.ecosystem)


class virtualWebSocketSensor(WebSocketSensor, virtualSensor):
    pass


virtual_sensor_models: dict[str, Type[virtualSensor]] = {
    hardware.__name__: hardware
    for hardware in [
        virtualAHT20,
        virtualDHT11,
        virtualDHT22,
        virtualVCNL4040,
        virtualVEML7700,
        virtualCapacitiveMoisture,
        virtualENS160,
        virtualWebSocketSensor,
    ]
}
