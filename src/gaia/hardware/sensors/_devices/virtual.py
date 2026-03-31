from __future__ import annotations

import typing as t

from gaia.hardware.virtual import VirtualDevice
from gaia.hardware.sensors._devices._compatibility import (
    AHTx0Device,
    DS18B20Device,
    DHTBaseDevice,
    SeesawDevice,
    VCNL4040Device,
    VEML7700Device,
)
from gaia.hardware._compatibility import add_noise

if t.TYPE_CHECKING:
    from gaia.virtual import VirtualEcosystem


class VirtualLightMixin:
    virtual_ecosystem: VirtualEcosystem

    @property
    def lux(self) -> float:
        self.virtual_ecosystem.measure()
        return round(add_noise(self.virtual_ecosystem.light))


class VirtualTemperatureMixin:
    virtual_ecosystem: VirtualEcosystem

    @property
    def temperature(self) -> float:
        self.virtual_ecosystem.measure()
        return round(add_noise(self.virtual_ecosystem.temperature), 2)


class VirtualHumidityMixin:
    virtual_ecosystem: VirtualEcosystem

    @property
    def humidity(self) -> float:
        self.virtual_ecosystem.measure()
        return round(add_noise(self.virtual_ecosystem.humidity), 2)


class VirtualDHTBaseDevice(VirtualDevice, VirtualTemperatureMixin, VirtualHumidityMixin, DHTBaseDevice):
    pass


class VirtualDHT11Device(VirtualDHTBaseDevice):
    pass


class VirtualDHT22Device(VirtualDHTBaseDevice):
    pass


class VirtualAHTx0Device(VirtualDevice, VirtualTemperatureMixin, VirtualHumidityMixin, AHTx0Device):
    def _readdata(self) -> None:
        pass

    @property
    def _temp(self) -> float:
        return self.temperature

    @property
    def _humidity(self) -> float:
        return self.humidity


class VirtualVEML7700Device(VirtualDevice, VirtualLightMixin, VEML7700Device):
    pass


class VirtualVCNL4040Device(VirtualDevice, VirtualLightMixin, VCNL4040Device):
    pass


class VirtualSeesawDevice(VirtualDevice, VirtualTemperatureMixin, VirtualHumidityMixin, SeesawDevice):
    pass


class VirtualDS18B20Device(VirtualDevice, VirtualTemperatureMixin, DS18B20Device):
    def get_data(self) -> float | None:
        return self.temperature
