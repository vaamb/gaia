from typing import Type

from adafruit_platformdetect import Board, Detector

_IS_RASPI: bool = Board(Detector()).any_raspberry_pi  # noqa

from gaia.hardware.abc import BaseSensor, Dimmer, Hardware, LightSensor, Switch
from gaia.hardware.actuators import ACTUATORS, gpioDimmable, gpioSwitch
from gaia.hardware.camera import CAMERA
from gaia.hardware.GPIO_sensors import DHT11, DHT22, GPIO_SENSORS
from gaia.hardware.I2C_sensors import CapacitiveMoisture, I2C_SENSORS, VEML7700
from gaia.hardware.multiplexers import MULTIPLEXERS, TCA9548A
from gaia.hardware.virtual_sensors import (
    virtualCapacitiveMoisture, virtualDHT11, virtualDHT22, VIRTUAL_SENSORS,
    virtualVEML7700
)

I2C_LIGHT_SENSORS: dict[str, Type[LightSensor]] = {
    hardware.__name__: hardware for hardware in [
        VEML7700,
        virtualVEML7700,
    ]
}

SENSORS: dict[str, Type[BaseSensor]] = {
    **VIRTUAL_SENSORS,
    **GPIO_SENSORS,
    **I2C_SENSORS,
}

CAMERA: dict[str, Type[Hardware]] = {

}

HARDWARE: dict[str, Type[Hardware]] = {
    **ACTUATORS,
    **CAMERA,
    **SENSORS,
}
