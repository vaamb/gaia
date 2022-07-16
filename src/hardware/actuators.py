from .ABC import gpioDimmer, gpioHardware, Switch
from config import Config

if Config.VIRTUALIZATION:
    from src.virtual import get_virtual_ecosystem


class gpioSwitch(gpioHardware, Switch):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._pin.init(mode=self.OUT)

    def turn_on(self) -> None:
        self._pin.value(val=1)
        if Config.VIRTUALIZATION:
            get_virtual_ecosystem(self.subroutine.ecosystem.uid)._light = True

    def turn_off(self) -> None:
        self._pin.value(val=0)
        if Config.VIRTUALIZATION:
            get_virtual_ecosystem(self.subroutine.ecosystem.uid)._light = False


class gpioDimmable(gpioSwitch, gpioDimmer):
    pass


ACTUATORS = {
    hardware.__name__: hardware for hardware in [
        gpioSwitch,
        gpioDimmable,
    ]
}
