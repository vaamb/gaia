from typing import Type

from gaia.exceptions import DeviceError
from gaia.hardware.abc import Actuator, Dimmer, Switch, WebSocketHardware


class WebSocketSwitch(Switch, WebSocketHardware):
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

class WebSocketDimmer(Dimmer, WebSocketHardware):
    async def set_pwm_level(self, level) -> bool:
        try:
            return await self._execute_action(
                {"action": "set_level", "data": level},
                f"Failed to set the level to {level}"
            )
        except (ConnectionError, DeviceError):
            return False


websocket_actuator_models: dict[str, Type[Actuator]] = {
    hardware.__name__: hardware
    for hardware in [
        WebSocketDimmer,
        WebSocketSwitch,
    ]
}
