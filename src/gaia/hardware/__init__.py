from typing import Type

from adafruit_platformdetect import Board, Detector

_IS_RASPI: bool = Board(Detector()).any_raspberry_pi  # noqa

from gaia.hardware.abc import Hardware
from gaia.hardware.actuators import actuator_models, gpioDimmable, gpioSwitch
from gaia.hardware.camera import camera_models
from gaia.hardware.sensors import sensor_models
from gaia.hardware.sensors.GPIO import DHT11, DHT22, gpio_sensor_models
from gaia.hardware.sensors.I2C import (
    CapacitiveMoisture, i1c_sensor_models, VEML7700
)
from gaia.hardware.multiplexers import MultiplexerModels, TCA9548A
from gaia.hardware.sensors.virtual import (
    virtualCapacitiveMoisture, virtualDHT11, virtualDHT22, virtual_sensor_models,
    virtualVEML7700
)


hardware_models: dict[str, Type[Hardware]] = {
    **actuator_models,
    **camera_models,
    **sensor_models,
}
