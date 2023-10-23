from gaia.hardware.abc import gpioDimmer, gpioHardware, Switch


class gpioSwitch(gpioHardware, Switch):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.pin.init(mode=self.OUT)

    def turn_on(self) -> None:
        self.pin.value(val=1)

    def turn_off(self) -> None:
        self.pin.value(val=0)


class gpioDimmable(gpioSwitch, gpioDimmer):
    pass


gpio_actuator_models = {
    hardware.__name__: hardware for hardware in [
        gpioDimmable,
        gpioSwitch,
    ]
}
