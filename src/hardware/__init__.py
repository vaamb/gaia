from adafruit_platformdetect import Board, Detector

_IS_RASPI = Board(Detector()).any_raspberry_pi

from .actuators import ACTUATORS, gpioDimmable, gpioSwitch
from .sensors import (
    CapacitiveMoisture, DHT11, DHT22, GPIO_SENSORS, I2C_SENSORS, VEML7700
)
from .virtual_sensors import (
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
