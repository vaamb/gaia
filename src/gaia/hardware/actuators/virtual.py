from gaia.hardware.abc import gpioDimmer
from gaia.hardware.actuators.GPIO import gpioSwitch
from gaia.hardware.virtual import virtualHardware
from gaia.virtual import get_virtual_ecosystem


class virtualgpioSwitch(virtualHardware, gpioSwitch):
    def turn_on(self) -> None:
        super().turn_on()
        if(
            self.subroutine is not None
            and self.subroutine.ecosystem.engine.config.app_config.VIRTUALIZATION
        ):
            get_virtual_ecosystem(self.subroutine.ecosystem.uid)._light = True

    def turn_off(self) -> None:
        super().turn_off()
        if(
            self.subroutine is not None
            and self.subroutine.ecosystem.engine.config.app_config.VIRTUALIZATION
        ):
            get_virtual_ecosystem(self.subroutine.ecosystem.uid)._light = False


class virtualgpioDimmable(virtualgpioSwitch, gpioDimmer):
    pass


virtual_actuator_models = {
    hardware.__name__: hardware for hardware in [
        virtualgpioDimmable,
        virtualgpioSwitch,
    ]
}
