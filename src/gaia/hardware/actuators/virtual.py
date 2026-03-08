from gaia.hardware.abc import Actuator
from gaia.hardware.actuators.GPIO import gpioDimmer, gpioSwitch
from gaia.hardware.virtual import virtualHardware


class virtualgpioSwitch(virtualHardware, gpioSwitch):
    __slots__ = ()


# Valid ignore: __slots__ layout conflict is a known CPython limitation with multiple inheritance; works at runtime
class virtualgpioDimmable(virtualgpioSwitch, gpioDimmer):  # ty: ignore[instance-layout-conflict]
    __slots__ = ()


virtual_actuator_models: dict[str, type[Actuator]] = {
    hardware.__name__: hardware
    for hardware in [
        virtualgpioDimmable,
        virtualgpioSwitch,
    ]
}
