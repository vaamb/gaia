from __future__ import annotations

import typing as t
from typing import Type

from gaia.hardware.abc import SensorMixin
from gaia.hardware.sensors.GPIO import DHTSensor
from gaia.hardware.sensors.I2C import (
    AHT20, CapacitiveMoisture, ENS160, VCNL4040, VEML7700)
from gaia.hardware.sensors.websocket import WebSocketSensor
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


class virtualSensor(virtualHardware, SensorMixin):
    pass


class virtualDHT(DHTSensor, virtualSensor):
    pass


class virtualDHT11(virtualDHT):
    def _get_device(self) -> VirtualDHT11Device:
        from gaia.hardware.sensors._devices.virtual import VirtualDHT11Device

        return VirtualDHT11Device(virtual_ecosystem=self.virtual_ecosystem)


class virtualDHT22(virtualDHT):
    def _get_device(self) -> VirtualDHT22Device:
        from gaia.hardware.sensors._devices.virtual import VirtualDHT22Device

        return VirtualDHT22Device(virtual_ecosystem=self.virtual_ecosystem)


class virtualAHT20(AHT20, virtualSensor):
    def _get_device(self) -> VirtualAHTx0Device:
        from gaia.hardware.sensors._devices.virtual import VirtualAHTx0Device

        return VirtualAHTx0Device(virtual_ecosystem=self.virtual_ecosystem)


class virtualVCNL4040(VCNL4040, virtualSensor):
    def _get_device(self) -> VirtualVCNL4040Device:
        from gaia.hardware.sensors._devices.virtual import VirtualVCNL4040Device

        return VirtualVCNL4040Device(virtual_ecosystem=self.virtual_ecosystem)


class virtualVEML7700(VEML7700, virtualSensor):
    def _get_device(self) -> VirtualVEML7700Device:
        from gaia.hardware.sensors._devices.virtual import VirtualVEML7700Device

        return VirtualVEML7700Device(virtual_ecosystem=self.virtual_ecosystem)


class virtualCapacitiveMoisture(CapacitiveMoisture, virtualSensor):
    def _get_device(self) -> VirtualSeesawDevice:
        from gaia.hardware.sensors._devices.virtual import VirtualSeesawDevice

        return VirtualSeesawDevice(virtual_ecosystem=self.virtual_ecosystem)


class virtualENS160(ENS160, virtualSensor):
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
