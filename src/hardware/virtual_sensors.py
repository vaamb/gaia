import random

from .base import gpioSensor, i2cSensor
from .physical_sensors import DHTSensor
from config import Config
from src.utils import random_sleep


if not Config.VIRTUALIZATION:
    from .rdm_measures import get_humidity, get_light, get_moisture, \
        get_temperature

else:
    from src.virtual import get_virtual_ecosystem
    from .rdm_measures import add_noise

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


    def get_moisture(engine, *args, **kwargs) -> float:
        return round(random.uniform(10, 55), 2)


class virtualSensor:
    def __init__(self, **kwargs):
        if Config.VIRTUALIZATION:
            get_virtual_ecosystem(self.subroutine.engine.uid, start=True)


class virtualDHT(gpioSensor, virtualSensor):
    def __init__(self, **kwargs):
        if not kwargs.get("measure", []):
            kwargs["measure"] = ["temperature", "humidity"]
        super().__init__(**kwargs)

        self._unit = kwargs.pop("unit", "celsius")

        self._raw_data = {}

    def _get_raw_data(self) -> tuple:
        random_sleep()
        humidity = get_humidity(self.subroutine.engine.uid)
        temperature = get_temperature(self.subroutine.engine.uid)
        return humidity, temperature

    def get_data(self):
        return DHTSensor.get_data(self)


class virtualDHT11(virtualDHT):
    MODEL = "virtualDHT11"


class virtualDHT22(virtualDHT):
    MODEL = "virtualDHT22"


class virtualVEML7700(i2cSensor, virtualSensor):
    MODEL = "virtualVEML7700"

    def get_data(self) -> list:
        random_sleep()
        return [{"name": "light",
                 "values": get_light(self.subroutine.engine.uid)}]


class virtualMega(virtualDHT):
    MODEL = "virtualMega"

    def __init__(self, **kwargs):
        if not kwargs.get("measure", []):
            kwargs["measure"] = ["temperature", "humidity", "light"]
        super().__init__(**kwargs)

    def get_data(self) -> list:
        data = super().get_data()
        if "light" in self._measure:
            data.append({"name": "light",
                         "values": get_light(self.subroutine.engine.uid)})
        return data


class virtualMoisture(gpioSensor, virtualSensor):
    MODEL = "virtualMoisture"

    def get_data(self) -> list:
        random_sleep()
        return [{"name": "moisture",
                 "values": get_moisture(self.subroutine.engine.uid)}]


VIRTUAL_SENSORS = {hardware.MODEL: hardware for hardware in
                   [virtualDHT11,
                    virtualDHT22,
                    virtualVEML7700,
                    virtualMega,
                    virtualMoisture]}
