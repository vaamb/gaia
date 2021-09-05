from .base import gpioHardware
from config import Config

if Config.VIRTUALIZATION:
    from src.virtual import get_virtual_ecosystem


# TODO: add model for when using pwm. In this case: address like GPIO_2:PWM_GPIO_12
class gpioSwitch(gpioHardware):
    MODEL = "gpioSwitch"

    def __init__(self, **kwargs) -> None:
        # uncomment if you want to overwrite the name of model
#        kwargs["model"] = self.MODEL
        super().__init__(**kwargs)
        self._pin.init(mode=self.OUT)

    def turn_on(self) -> None:
        self._pin.value(val=1)
        if Config.VIRTUALIZATION:
            get_virtual_ecosystem(self._subroutine._engine.uid)._light = True

    def turn_off(self) -> None:
        self._pin.value(val=0)
        if Config.VIRTUALIZATION:
            get_virtual_ecosystem(self._subroutine._engine.uid)._light = False


ACTUATORS = {hardware.MODEL: hardware for hardware in
             [gpioSwitch]}
