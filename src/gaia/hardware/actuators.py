from gaia.config import get_config
from gaia.hardware.abc import gpioDimmer, gpioHardware, Switch


if get_config().VIRTUALIZATION:
    from gaia.virtual import get_virtual_ecosystem


class gpioSwitch(gpioHardware, Switch):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.pin.init(mode=self.OUT)

    def turn_on(self) -> None:
        self.pin.value(val=1)
        if get_config().VIRTUALIZATION:
            get_virtual_ecosystem(self.subroutine.ecosystem.uid)._light = True

    def turn_off(self) -> None:
        self.pin.value(val=0)
        if get_config().VIRTUALIZATION:
            get_virtual_ecosystem(self.subroutine.ecosystem.uid)._light = False


class gpioDimmable(gpioSwitch, gpioDimmer):
    pass


actuator_models = {
    hardware.__name__: hardware for hardware in [
        gpioDimmable,
        gpioSwitch,
    ]
}
