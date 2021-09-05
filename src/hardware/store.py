from .actuators import ACTUATORS
from .physical_sensors import GPIO_SENSORS, I2C_SENSORS
from .virtual_sensors import VIRTUAL_SENSORS


SENSORS = {**VIRTUAL_SENSORS,
           **GPIO_SENSORS,
           **I2C_SENSORS}

HARDWARE_AVAILABLE = {**ACTUATORS,
                      **SENSORS}
