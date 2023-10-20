import typing as t
from typing import Type

from gaia.hardware.abc import BaseSensor
from gaia.hardware.virtual import virtualHardware
from gaia.hardware.sensors.GPIO import DHTSensor
from gaia.hardware.sensors.I2C import (
    AHT20, CapacitiveMoisture, VCNL4040, VEML7700)


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.hardware._compatibility import (
        AHTx0, DHT11 as _DHT11, DHT22 as _DHT22, Seesaw, VCNL4040 as _VCNL4040,
        VEML7700 as _VEML7700)


class virtualSensor(virtualHardware, BaseSensor):
    pass


class virtualDHT(DHTSensor, virtualSensor):
    pass


class virtualDHT11(virtualDHT):
    def _get_device(self) -> "_DHT11":
        from gaia.hardware._compatibility import DHT11 as _DHT11
        return _DHT11(ecosystem_uid=self.subroutine.ecosystem.uid)


class virtualDHT22(virtualDHT):
    def _get_device(self) -> "_DHT22":
        from gaia.hardware._compatibility import DHT22 as _DHT22
        return _DHT22(ecosystem_uid=self.subroutine.ecosystem.uid)


class virtualAHT20(AHT20, virtualSensor):
    def _get_device(self) -> "AHTx0":
        from gaia.hardware._compatibility import AHTx0
        return AHTx0(ecosystem_uid=self.subroutine.ecosystem.uid)


class virtualVCNL4040(VCNL4040, virtualSensor):
    def _get_device(self) -> "_VCNL4040":
        from gaia.hardware._compatibility import VCNL4040 as _VCNL4040
        return _VCNL4040(ecosystem_uid=self.subroutine.ecosystem.uid)


class virtualVEML7700(VEML7700, virtualSensor):
    def _get_device(self) -> "_VEML7700":
        from gaia.hardware._compatibility import VEML7700 as _VEML7700
        return _VEML7700(ecosystem_uid=self.subroutine.ecosystem.uid)


class virtualCapacitiveMoisture(CapacitiveMoisture, virtualSensor):
    def _get_device(self) -> "Seesaw":
        from gaia.hardware._compatibility import Seesaw
        return Seesaw(ecosystem_uid=self.subroutine.ecosystem.uid)


virtual_sensor_models:  dict[str, Type[virtualSensor]]= {
    hardware.__name__: hardware for hardware in [
        virtualAHT20,
        virtualDHT11,
        virtualDHT22,
        virtualVCNL4040,
        virtualVEML7700,
        virtualCapacitiveMoisture,
    ]
}
