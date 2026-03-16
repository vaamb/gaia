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
    from gaia.hardware.sensors._devices.virtual import (
        VirtualAHTx0Device,
        VirtualDHT11Device,
        VirtualDHT22Device,
        VirtualSeesawDevice,
        VirtualVCNL4040Device,
        VirtualVEML7700Device,
    )
    from gaia.hardware.sensors._devices._compatibility import (
        ENS160Device
    )


# Valid ignore: __slots__ layout conflict is a known CPython limitation with multiple inheritance; works at runtime
class virtualSensor(virtualHardware, BaseSensor):  # ty: ignore[instance-layout-conflict]
    __slots__ = ()


class virtualDHT(DHTSensor, virtualSensor):
    __slots__ = ()


class virtualDHT11(virtualDHT):
    __slots__ = ()

    def _get_device(self) -> VirtualDHT11Device:
        from gaia.hardware.sensors._devices.virtual import VirtualDHT11Device

        return VirtualDHT11Device(ecosystem=self.ecosystem)


class virtualDHT22(virtualDHT):
    __slots__ = ()

    def _get_device(self) -> VirtualDHT22Device:
        from gaia.hardware.sensors._devices.virtual import VirtualDHT22Device

        return VirtualDHT22Device(ecosystem=self.ecosystem)


class virtualAHT20(AHT20, virtualSensor):
    __slots__ = ()

    def _get_device(self) -> VirtualAHTx0Device:
        from gaia.hardware.sensors._devices.virtual import VirtualAHTx0Device

        return VirtualAHTx0Device(ecosystem=self.ecosystem)


class virtualVCNL4040(VCNL4040, virtualSensor):
    __slots__ = ()

    def _get_device(self) -> VirtualVCNL4040Device:
        from gaia.hardware.sensors._devices.virtual import VirtualVCNL4040Device

        return VirtualVCNL4040Device(ecosystem=self.ecosystem)


class virtualVEML7700(VEML7700, virtualSensor):
    __slots__ = ()

    def _get_device(self) -> VirtualVEML7700Device:
        from gaia.hardware.sensors._devices.virtual import VirtualVEML7700Device

        return VirtualVEML7700Device(ecosystem=self.ecosystem)


class virtualCapacitiveMoisture(CapacitiveMoisture, virtualSensor):
    __slots__ = ()

    def _get_device(self) -> VirtualSeesawDevice:
        from gaia.hardware.sensors._devices.virtual import VirtualSeesawDevice

        return VirtualSeesawDevice(ecosystem=self.ecosystem)


class virtualENS160(ENS160, virtualSensor):
    __slots__ = ()

    def _get_device(self) -> ENS160Device:
        # TODO: design and use a virtual ENS160Device
        from gaia.hardware.sensors._devices._compatibility import ENS160Device

        return ENS160Device()


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
