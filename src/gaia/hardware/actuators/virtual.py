from gaia.hardware.actuators.GPIO import gpioDimmer, gpioSwitch
from gaia.hardware.virtual import virtualHardware


class virtualgpioSwitch(virtualHardware, gpioSwitch):
    pass


class virtualgpioDimmable(virtualgpioSwitch, gpioDimmer):
    pass


virtual_actuator_models = {
    hardware.__name__: hardware
    for hardware in [
        virtualgpioDimmable,
        virtualgpioSwitch,
    ]
}
