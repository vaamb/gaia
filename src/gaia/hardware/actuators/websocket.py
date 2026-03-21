from typing import Type

from gaia.exceptions import DeviceError
from gaia.hardware.abc import ActuatorMixin, DimmerMixin, Hardware, SwitchMixin, WebSocketAddressMixin


class WebSocketSwitch(WebSocketAddressMixin, SwitchMixin, Hardware):
    async def turn_on(self) -> bool:
        try:
            return await self._execute_action(
                {"action": "turn_actuator", "data": "on"},
                "Failed to turn on the switch"
            )
        except (ConnectionError, DeviceError):
            return False

    async def turn_off(self) -> bool:
        try:
            return await self._execute_action(
                {"action": "turn_actuator", "data": "off"},
                "Failed to turn off the switch"
            )
        except (ConnectionError, DeviceError):
            return False

    async def get_status(self) -> bool:
        try:
            return await self._execute_action(
                {"action": "get_status"},
                "Failed to get status"
            )
        except (ConnectionError, DeviceError):
            # TODO: find a better way to handle error
            return False

class WebSocketDimmer(WebSocketAddressMixin, DimmerMixin, Hardware):
    async def set_pwm_level(self, level) -> bool:
        try:
            return await self._execute_action(
                {"action": "set_level", "data": level},
                f"Failed to set the level to {level}"
            )
        except (ConnectionError, DeviceError):
            return False

    async def get_pwm_level(self) -> int | float:
        try:
            return await self._execute_action(
                {"action": "get_pwm_level"},
                "Failed to get PWM level"
            )
        except (ConnectionError, DeviceError):
            # TODO: find a better way to handle error
            return 100.0


websocket_actuator_models: dict[str, Type[ActuatorMixin]] = {
    hardware.__name__: hardware
    for hardware in [
        WebSocketDimmer,
        WebSocketSwitch,
    ]
}
