from gaia.hardware.virtual import VirtualDevice
from gaia.hardware.sensors._devices._compatibility import (
    AHTx0Device,
    BS18B20Device,
    DHTBaseDevice,
    SeesawDevice,
    VCNL4040Device,
    VEML7700Device,
)
from gaia.hardware._compatibility import add_noise


class VirtualLightMixin:
    @property
    def lux(self) -> float:
        self.virtual_ecosystem.measure()
        return round(add_noise(self.virtual_ecosystem.light))


class VirtualTemperatureMixin:
    @property
    def temperature(self) -> float:
        self.virtual_ecosystem.measure()
        return round(add_noise(self.virtual_ecosystem.temperature), 2)


class VirtualHumidityMixin:
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


class VirtualBS18B20Device(VirtualDevice, VirtualTemperatureMixin, BS18B20Device):
    def get_data(self) -> float | None:
        return self.temperature
