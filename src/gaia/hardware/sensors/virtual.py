import typing as t
from typing import Type

from gaia.config import get_config
from gaia.hardware.abc import BaseSensor, LightSensor
from gaia.hardware.sensors.GPIO import DHTSensor
from gaia.hardware.sensors.I2C import VEML7700, CapacitiveMoisture


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.hardware._compatibility import (
        DHTBase as _DHTBase, DHT11 as _DHT11, DHT22 as _DHT22,
        Seesaw, VEML7700 as _VEML7700)


class virtualSensor(BaseSensor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if get_config().VIRTUALIZATION:
            from gaia.virtual import get_virtual_ecosystem
            get_virtual_ecosystem(self.subroutine.ecosystem.uid, start=True)


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


class virtualVEML7700(VEML7700, virtualSensor, LightSensor):
    def _get_device(self) -> "_VEML7700":
        from gaia.hardware._compatibility import VEML7700 as _VEML7700
        return _VEML7700(ecosystem_uid=self.subroutine.ecosystem.uid)


class virtualCapacitiveMoisture(CapacitiveMoisture, virtualSensor):
    def _get_device(self) -> "Seesaw":
        from gaia.hardware._compatibility import Seesaw
        return Seesaw(ecosystem_uid=self.subroutine.ecosystem.uid)


virtual_sensor_models:  dict[str, Type[virtualSensor]]= {
    hardware.__name__: hardware for hardware in [
        virtualDHT11,
        virtualDHT22,
        virtualVEML7700,
        virtualCapacitiveMoisture,
    ]
}
