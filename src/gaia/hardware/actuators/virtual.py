import typing as t

from gaia.hardware.abc import (
    Actuator, ActuatorMixin, DimmableSwitchMixin, DimmerMixin, SwitchMixin)
from gaia.hardware.actuators.GPIO import gpioDimmable, gpioDimmer, gpioSwitch
from gaia.hardware.actuators.websocket import WebSocketDimmer, WebSocketSwitch
from gaia.hardware.virtual import virtualHardwareMixin


class virtualActuator(virtualHardwareMixin, ActuatorMixin):
    if t.TYPE_CHECKING:
        uid: str
        groups: set[str]

    async def _on_initialize(self) -> None:
        # Registration must be done before other registrations that might
        #  interact with the virtual ecosystem
        self.virtual_ecosystem.register_actuator(self.uid, self.groups)
        await super()._on_initialize()

    async def _on_terminate(self) -> None:
        await super()._on_terminate()
        self.virtual_ecosystem.unregister_actuator(self.uid)


class virtualSwitch(virtualActuator, SwitchMixin):
    async def turn_on(self) -> bool:
        self.virtual_ecosystem.set_actuator_status(self.uid, True)
        return True

    async def turn_off(self) -> bool:
        self.virtual_ecosystem.set_actuator_status(self.uid, False)
        return True

    async def get_status(self) -> bool:
        return self.virtual_ecosystem.get_actuator_status(self.uid)


class virtualgpioSwitch(virtualSwitch, gpioSwitch):
    pass


class virtualWebSocketSwitch(virtualSwitch, WebSocketSwitch):
    pass


class virtualDimmer(virtualActuator, DimmerMixin):
    async def set_pwm_level(self, level: float | int) -> bool:
        self.virtual_ecosystem.set_actuator_level(self.uid, level)
        return True

    async def get_pwm_level(self) -> int | float:
        return self.virtual_ecosystem.get_actuator_level(self.uid)


class virtualgpioDimmer(virtualDimmer, gpioDimmer):
    pass


class virtualWebSocketDimmer(virtualDimmer, WebSocketDimmer):
    pass


class virtualDimmable(virtualSwitch, virtualDimmer):
    pass


class virtualgpioDimmable(virtualDimmer, gpioDimmable, DimmableSwitchMixin):
    pass


virtual_actuator_models: dict[str, type[Actuator]] = {
    hardware.__name__: hardware
    for hardware in [
        virtualgpioDimmable,
        virtualgpioDimmer,
        virtualgpioSwitch,
        virtualWebSocketDimmer,
        virtualWebSocketSwitch,
    ]
}
