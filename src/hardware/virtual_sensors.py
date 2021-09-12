import random

from .base import baseSensor
from config import Config
from src.utils import get_absolute_humidity, get_dew_point, random_sleep


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


class virtualSensor(baseSensor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if Config.VIRTUALIZATION:
            get_virtual_ecosystem(self.subroutine.engine.uid, start=True)


class virtualDHT(virtualSensor):
    def get_data(self) -> dict:
        random_sleep()
        data = {
            "temperature": get_temperature(self.subroutine.engine.uid),
            "humidity": get_humidity(self.subroutine.engine.uid),
        }
        if "dew_point" in self._measure:
            data["dew_point"] = get_dew_point(
                data["temperature"], data["humidity"])
        if "absolute_humidity" in self._measure:
            data["absolute_humidity"] = get_absolute_humidity(
                data["temperature"], data["humidity"])
        return data


class virtualDHT11(virtualDHT):
    MODEL = "virtualDHT11"


class virtualDHT22(virtualDHT):
    MODEL = "virtualDHT22"


class virtualVEML7700(virtualSensor):
    MODEL = "virtualVEML7700"

    def get_data(self) -> dict:
        random_sleep()
        return {
            "light": get_light(self.subroutine.engine.uid),
        }


class virtualMega(virtualSensor):
    MODEL = "virtualMega"

    def get_data(self) -> dict:
        random_sleep()
        data = {
            "temperature": get_temperature(self.subroutine.engine.uid),
            "humidity": get_humidity(self.subroutine.engine.uid),
            "light": get_light(self.subroutine.engine.uid),
        }
        if "dew_point" in self._measure:
            data["dew_point"] = get_dew_point(
                data["temperature"], data["humidity"])
        if "absolute_humidity" in self._measure:
            data["absolute_humidity"] = get_absolute_humidity(
                data["temperature"], data["humidity"])
        return data


class virtualMoisture(virtualSensor):
    MODEL = "virtualMoisture"

    def get_data(self) -> dict:
        random_sleep()
        return {
            "moisture": get_moisture(self.subroutine.engine.uid),
        }


VIRTUAL_SENSORS = {hardware.MODEL: hardware for hardware in
                   [virtualDHT11,
                    virtualDHT22,
                    virtualVEML7700,
                    virtualMega,
                    virtualMoisture]}
