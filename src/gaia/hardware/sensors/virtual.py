import typing as t
from typing import Type

import gaia_validators as gv

from gaia.hardware.abc import BaseSensor, Measure
from gaia.hardware.sensors.GPIO import DHTSensor
from gaia.hardware.sensors.I2C import (
    AHT20, CapacitiveMoisture, ENS160, VCNL4040, VEML7700)
from gaia.hardware.sensors.websocket import WebSocketSensor
from gaia.hardware.utils import hardware_logger
from gaia.hardware.virtual import virtualHardware


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.hardware._compatibility import (
        AHTx0,
        DHT11 as _DHT11,
        DHT22 as _DHT22,
        ENS160 as _ENS160,
        Seesaw,
        VCNL4040 as _VCNL4040,
        VEML7700 as _VEML7700,
    )


class virtualSensor(virtualHardware, BaseSensor):
    pass


class virtualDHT(DHTSensor, virtualSensor):
    pass


class virtualDHT11(virtualDHT):
    def _get_device(self) -> "_DHT11":
        from gaia.hardware._compatibility import DHT11 as _DHT11

        if not self.ecosystem:
            hardware_logger.warning(
                f"'{self}' did not receive any ecosystem, Virtualization disabled.")
        return _DHT11(ecosystem=self.ecosystem)


class virtualDHT22(virtualDHT):
    def _get_device(self) -> "_DHT22":
        from gaia.hardware._compatibility import DHT22 as _DHT22

        if not self.ecosystem:
            hardware_logger.warning(
                f"'{self}' did not receive any ecosystem, Virtualization disabled.")
        return _DHT22(ecosystem=self.ecosystem)


class virtualAHT20(AHT20, virtualSensor):
    def _get_device(self) -> "AHTx0":
        from gaia.hardware._compatibility import AHTx0

        if not self.ecosystem:
            hardware_logger.warning(
                f"'{self}' did not receive any ecosystem, Virtualization disabled.")
        return AHTx0(ecosystem=self.ecosystem)


class virtualVCNL4040(VCNL4040, virtualSensor):
    def _get_device(self) -> "_VCNL4040":
        from gaia.hardware._compatibility import VCNL4040 as _VCNL4040

        if not self.ecosystem:
            hardware_logger.warning(
                f"'{self}' did not receive any ecosystem, Virtualization disabled.")
        return _VCNL4040(ecosystem=self.ecosystem)


class virtualVEML7700(VEML7700, virtualSensor):
    def _get_device(self) -> "_VEML7700":
        from gaia.hardware._compatibility import VEML7700 as _VEML7700

        if not self.ecosystem:
            hardware_logger.warning(
                f"'{self}' did not receive any ecosystem, Virtualization disabled.")
        return _VEML7700(ecosystem=self.ecosystem)


class virtualCapacitiveMoisture(CapacitiveMoisture, virtualSensor):
    def _get_device(self) -> "Seesaw":
        from gaia.hardware._compatibility import Seesaw as _Seesaw

        if not self.ecosystem:
            hardware_logger.warning(
                f"'{self}' did not receive any ecosystem, Virtualization disabled.")
        return _Seesaw(ecosystem=self.ecosystem)


class virtualENS160(ENS160, virtualSensor):
    def _get_device(self) -> "_ENS160":
        from gaia.hardware._compatibility import ENS160 as _ENS160

        if not self.ecosystem:
            hardware_logger.warning(
                f"'{self}' did not receive any ecosystem, Virtualization disabled.")
        return _ENS160(ecosystem=self.ecosystem)


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
