import random

from .ABC import BaseSensor, gpioSensor, PlantLevelHardware
from .compatibility import (
    DHTBase as cDHTBase, DHT11 as cDHT11, DHT22 as cDHT22, VEML7700 as cVEML7700
)
from .random_measures import random_sleep
from .sensors import DHTSensor, VEML7700
from config import Config


if Config.VIRTUALIZATION:
    from src.virtual import get_virtual_ecosystem
    from .random_measures import add_noise

    def get_temperature(ecosystem_uid, *args, **kwargs) -> float:
        virtual_ecosystem = get_virtual_ecosystem(ecosystem_uid, start=True)
        virtual_ecosystem.measure()
        return round(add_noise(virtual_ecosystem.temperature), 2)

    def get_humidity(ecosystem_uid, *args, **kwargs) -> float:
        virtual_ecosystem = get_virtual_ecosystem(ecosystem_uid, start=True)
        virtual_ecosystem.measure()
        return round(add_noise(virtual_ecosystem.humidity), 2)

    def get_light(ecosystem_uid, *args, **kwargs) -> float:
        virtual_ecosystem = get_virtual_ecosystem(ecosystem_uid, start=True)
        virtual_ecosystem.measure()
        return round(add_noise(virtual_ecosystem.lux))

    def get_moisture(ecosystem_uid, plant_uid, *args, **kwargs) -> float:
        return round(random.uniform(10, 55), 2)

else:
    from .random_measures import (
        get_humidity, get_light, get_moisture, get_temperature
    )


class virtualSensor(BaseSensor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if Config.VIRTUALIZATION:
            get_virtual_ecosystem(self.subroutine.ecosystem.uid, start=True)

    def get_data(self) -> list:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )


class virtualDHT(DHTSensor, virtualSensor):
    def _get_device(self):
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )


class virtualDHT11(virtualDHT):
    def _get_device(self) -> cDHTBase:
        return cDHT11()


class virtualDHT22(virtualDHT):
    def _get_device(self) -> cDHTBase:
        return cDHT22()


class virtualVEML7700(VEML7700, virtualSensor):
    def _get_device(self):
        return cVEML7700()


class virtualMega(virtualDHT):
    def __init__(self, *args, **kwargs):
        if not kwargs.get("measure", []):
            kwargs["measure"] = ["temperature", "humidity", "light"]
        super().__init__(*args, **kwargs)

    def _get_device(self) -> cDHTBase:
        return cDHTBase(self._pin, use_pulseio=False)

    def get_data(self) -> list:
        data = super().get_data()
        if "light" in self._measure:
            data.append({"name": "light",
                         "value": get_light(self.subroutine.ecosystem.uid)})
        return data


class virtualMoisture(gpioSensor, virtualSensor, PlantLevelHardware):
    def get_data(self) -> list:
        random_sleep()
        return [{"name": "moisture",
                 "value": get_moisture(self.subroutine.ecosystem.uid, self._plant)}]


VIRTUAL_SENSORS = {
    hardware.__name__: hardware for hardware in [
        virtualDHT11,
        virtualDHT22,
        virtualVEML7700,
        virtualMega,
        virtualMoisture,
    ]
}
