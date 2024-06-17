from __future__ import annotations

import typing as t

from anyio.to_thread import run_sync

from gaia.hardware.abc import AddressType, Dimmer, gpioHardware, Switch
from gaia.hardware.utils import is_raspi


if t.TYPE_CHECKING:  # pragma: no cover
    if is_raspi():
        import pwmio
        from adafruit_blinka.microcontroller.bcm283x.pin import Pin
    else:
        from gaia.hardware._compatibility import Pin, pwmio


class gpioSwitch(gpioHardware, Switch):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.pin.init(mode=self.OUT)

    def _turn_on(self) -> None:
        self.pin.value(val=1)

    async def turn_on(self) -> None:
        await run_sync(self._turn_on)

    def _turn_off(self) -> None:
        self.pin.value(val=0)

    async def turn_off(self) -> None:
        await run_sync(self._turn_off)


class gpioDimmer(gpioHardware, Dimmer):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not self._address_book.secondary.type == AddressType.GPIO:  # pragma: no cover
            raise ValueError(
                "gpioDimmable address must be of type"
                "'addressType1_addressNum1:GPIO_pinNumber'"
            )
        self._pwm_pin: "Pin" | None = None
        self._dimmer: "pwmio.PWMOut" | None = None

    def _get_dimmer(self) -> "pwmio.PWMOut":
        if is_raspi():
            try:
                import pwmio
            except ImportError:
                raise RuntimeError(
                    "Adafruit blinka package is required. Run `pip install "
                    "adafruit-blinka` in your virtual env`."
                )
        else:
            from gaia.hardware._compatibility import pwmio
        return pwmio.PWMOut(self.pwm_pin, frequency=100, duty_cycle=0)

    def _set_pwm_level(self, duty_cycle_in_percent: float | int) -> None:
        duty_cycle_in_16_bit = duty_cycle_in_percent / 100 * (2**16 - 1)
        self.dimmer.duty_cycle = duty_cycle_in_16_bit

    async def set_pwm_level(self, duty_cycle_in_percent: float | int) -> None:
        await run_sync(self._set_pwm_level, duty_cycle_in_percent)

    @property
    def pwm_pin(self) -> "Pin":
        if not self._pwm_pin:
            self._pwm_pin = self._get_pin(self._address_book.secondary.main)
        return self._pwm_pin

    @property
    def dimmer(self) -> "pwmio.PWMOut":
        if not self._dimmer:
            self._dimmer = self._get_dimmer()
        return self._dimmer


class gpioDimmable(gpioSwitch, gpioDimmer):
    pass


gpio_actuator_models = {
    hardware.__name__: hardware for hardware in [
        gpioDimmable,
        gpioDimmer,
        gpioSwitch,
    ]
}
