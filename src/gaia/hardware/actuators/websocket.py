from typing import Type

from gaia.hardware.abc import Dimmer, Hardware, Switch, WebSocketHardware


class WebSocketSwitch(Switch, WebSocketHardware):
    async def turn_on(self) -> bool:
        return await self._execute_action(
            {"action": "turn_actuator", "data": "on"},
            "Failed to turn on the switch"
        )

    async def turn_off(self) -> bool:
        return await self._execute_action(
            {"action": "turn_actuator", "data": "off"},
            "Failed to turn off the switch"
        )


class WebSocketDimmer(Dimmer, WebSocketHardware):
    async def set_pwm_level(self, level) -> bool:
        return await self._execute_action(
            {"action": "set_level", "data": level},
            f"Failed to set the level to {level}"
        )


websocket_actuator_models: dict[str, Type[Hardware]] = {
    hardware.__name__: hardware
    for hardware in [
        WebSocketDimmer,
        WebSocketSwitch,
    ]
}
