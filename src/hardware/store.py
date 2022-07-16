"""Module to access all the hardware available"""

from .actuators import ACTUATORS
from .sensors import GPIO_SENSORS, I2C_SENSORS, VEML7700
from .virtual_sensors import VIRTUAL_SENSORS, virtualVEML7700


I2C_LIGHT_SENSORS = {
    hardware.__name__: hardware for hardware in [
        VEML7700,
        virtualVEML7700,
    ]
}

SENSORS = {
    **VIRTUAL_SENSORS,
    **GPIO_SENSORS,
    **I2C_SENSORS,
}

HARDWARE_AVAILABLE = {
    **ACTUATORS,
    **SENSORS,
}
