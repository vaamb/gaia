import typing as t

from .ABC import BaseSensor
from .sensors import DHTSensor, VEML7700, CapacitiveMoisture
from config import Config


if t.TYPE_CHECKING:
    from ._compatibility import (
        DHTBase as _DHTBase, DHT11 as _DHT11, DHT22 as _DHT22,
        Seesaw, VEML7700 as _VEML7700
    )


class virtualSensor(BaseSensor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if Config.VIRTUALIZATION:
            from ..virtual import get_virtual_ecosystem
            get_virtual_ecosystem(self.subroutine.ecosystem.uid, start=True)

    def get_data(self) -> list:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )


class virtualDHT(DHTSensor, virtualSensor):
    def _get_device(self) -> "_DHTBase":
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )


class virtualDHT11(virtualDHT):
    def _get_device(self) -> "_DHT11":
        from ._compatibility import DHT11 as _DHT11
        return _DHT11(ecosystem_uid=self.subroutine.ecosystem.uid)


class virtualDHT22(virtualDHT):
    def _get_device(self) -> "_DHT22":
        from ._compatibility import DHT22 as _DHT22
        return _DHT22(ecosystem_uid=self.subroutine.ecosystem.uid)


class virtualVEML7700(VEML7700, virtualSensor):
    def _get_device(self) -> "_VEML7700":
        from ._compatibility import VEML7700 as _VEML7700
        return _VEML7700(ecosystem_uid=self.subroutine.ecosystem.uid)


class virtualCapacitiveMoisture(CapacitiveMoisture, virtualSensor):
    def _get_device(self) -> "Seesaw":
        from ._compatibility import Seesaw
        return Seesaw(ecosystem_uid=self.subroutine.ecosystem.uid)


VIRTUAL_SENSORS = {
    hardware.__name__: hardware for hardware in [
        virtualCapacitiveMoisture,
        virtualDHT11,
        virtualDHT22,
        virtualVEML7700,
    ]
}
