from __future__ import annotations

from math import isclose
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
    __slots__ = ()

    def _init_pin(self) -> None:
        self.pin.init(mode=self.OUT)

    async def _on_initialize(self) -> None:
        await super()._on_initialize()
        await run_sync(self._init_pin)

    def _turn_on(self) -> bool:
        self.pin.value(val=1)
        return self.pin.value() == 1

    async def turn_on(self) -> bool:
        return await run_sync(self._turn_on)

    def _turn_off(self) -> bool:
        self.pin.value(val=0)
        return self.pin.value() == 0

    async def turn_off(self) -> bool:
        return await run_sync(self._turn_off)


class gpioDimmer(gpioHardware, Dimmer):
    __slots__ = ("_dimmer",)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._dimmer: pwmio.PWMOut | None = None

    @property
    def dimmer(self) -> pwmio.PWMOut:
        if not self._dimmer:
            self._dimmer = self._get_dimmer()
        return self._dimmer

    def _get_dimmer(self) -> pwmio.PWMOut:
        if is_raspi():  # pragma: no cover
            try:
                import pwmio
            except ImportError:
                raise RuntimeError(
                    "Adafruit blinka package is required. Run `pip install "
                    "adafruit-blinka` in your virtual env`."
                )
        else:
            from gaia.hardware._compatibility import pwmio
        return pwmio.PWMOut(self.pin, frequency=100, duty_cycle=0)

    async def set_pwm_level(self, duty_cycle_in_percent: float | int) -> bool:
        return await run_sync(self._set_pwm_level, duty_cycle_in_percent)

    def _set_pwm_level(self, duty_cycle_in_percent: float | int) -> bool:
        duty_cycle_in_16_bit = duty_cycle_in_percent / 100 * (2**16 - 1)
        self.dimmer.duty_cycle = duty_cycle_in_16_bit
        # Allow a 0.5% tolerance
        return isclose(self.dimmer.duty_cycle, duty_cycle_in_16_bit, rel_tol=0.005)


class gpioDimmable(gpioSwitch, gpioDimmer):
    __slots__ = ()


gpio_actuator_models = {
    hardware.__name__: hardware
    for hardware in [
        gpioDimmable,
        gpioDimmer,
        gpioSwitch,
    ]
}
