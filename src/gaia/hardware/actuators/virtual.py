from gaia.hardware.abc import Actuator
from gaia.hardware.actuators.GPIO import gpioDimmer, gpioSwitch
from gaia.hardware.virtual import virtualHardware


class virtualgpioSwitch(virtualHardware, gpioSwitch):
    __slots__ = ()


class virtualgpioDimmable(virtualgpioSwitch, gpioDimmer):
    __slots__ = ()


virtual_actuator_models: dict[str, type[Actuator]] = {
    hardware.__name__: hardware
    for hardware in [
        virtualgpioDimmable,
        virtualgpioSwitch,
    ]
}
