from typing import Type

from websockets import ConnectionClosedOK

import gaia_validators as gv

from gaia.hardware.abc import Dimmer, Hardware, Switch, WebSocketHardware


class WebSocketSwitch(Switch, WebSocketHardware):
    async def turn_on(self) -> None:
        try:
            response = await self._send_msg_and_wait({"action": "turn_actuator", "data": "on"})
            response = gv.RequestResult.model_validate(response)
        except (ConnectionError, ConnectionClosedOK):
            self._logger.error("Could not connect to the device")
        else:
            if response.status != gv.Result.success:
                base_msg = "Failed to turn on the switch"
                if response.message:
                    base_msg = f"{base_msg}. Error msg: `{response.message}`."
                self._logger.error(base_msg)


    async def turn_off(self) -> None:
        try:
            response = await self._send_msg_and_wait({"action": "turn_actuator", "data": "off"})
            response = gv.RequestResult.model_validate(response)
        except (ConnectionError, ConnectionClosedOK):
            self._logger.error("Could not connect to the device")
        else:
            if response.status != gv.Result.success:
                base_msg = "Failed to turn off the switch"
                if response.message:
                    base_msg = f"{base_msg}. Error msg: `{response.message}`."
                self._logger.error(base_msg)


class WebSocketDimmer(Dimmer, WebSocketHardware):
    async def set_pwm_level(self, level) -> None:
        try:
            response = await self._send_msg_and_wait({"action": "set_level", "data": level})
            response = gv.RequestResult.model_validate(response)
        except (ConnectionError, ConnectionClosedOK):
            self._logger.error("Could not connect to the device")
        else:
            if response.status != gv.Result.success:
                base_msg = f"Failed to set the level to {level}"
                if response.message:
                    base_msg = f"{base_msg}. Error msg: `{response.message}`."
                self._logger.error(base_msg)


websocket_actuator_models: dict[str, Type[Hardware]] = {
    hardware.__name__: hardware
    for hardware in [
        WebSocketDimmer,
        WebSocketSwitch,
    ]
}
