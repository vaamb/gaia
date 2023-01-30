from adafruit_platformdetect import Board, Detector

_IS_RASPI: bool = Board(Detector()).any_raspberry_pi  # noqa

from gaia.hardware.actuators import ACTUATORS, gpioDimmable, gpioSwitch
from gaia.hardware.GPIO_sensors import DHT11, DHT22, GPIO_SENSORS
from gaia.hardware.I2C_sensors import CapacitiveMoisture, I2C_SENSORS, VEML7700
from gaia.hardware.multiplexers import MULTIPLEXERS, TCA9548A
from gaia.hardware.virtual_sensors import (
    virtualCapacitiveMoisture, virtualDHT11, virtualDHT22, VIRTUAL_SENSORS,
    virtualVEML7700
)

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
