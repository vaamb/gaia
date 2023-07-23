from typing import Type

from gaia.hardware.abc import Hardware
from gaia.hardware.actuators import actuator_models, gpioDimmable, gpioSwitch
from gaia.hardware.camera import camera_models
from gaia.hardware.sensors import sensor_models
from gaia.hardware.sensors.GPIO import DHT11, DHT22, gpio_sensor_models
from gaia.hardware.sensors.I2C import (
    CapacitiveMoisture, i2c_sensor_models, VEML7700)
from gaia.hardware.multiplexers import multiplexer_models, TCA9548A
from gaia.hardware.sensors.virtual import (
    virtualCapacitiveMoisture, virtualDHT11, virtualDHT22, virtual_sensor_models,
    virtualVEML7700)


hardware_models: dict[str, Type[Hardware]] = {
    **actuator_models,
    **camera_models,
    **sensor_models,
}
