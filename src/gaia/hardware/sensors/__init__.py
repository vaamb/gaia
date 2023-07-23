from typing import Type

from gaia.hardware.abc import BaseSensor
from gaia.hardware.sensors.GPIO import gpio_sensor_models
from gaia.hardware.sensors.I2C import i2c_sensor_models
from gaia.hardware.sensors.virtual import virtual_sensor_models


sensor_models: dict[str, Type[BaseSensor]] = {
    **gpio_sensor_models,
    **i2c_sensor_models,
    **virtual_sensor_models,
}