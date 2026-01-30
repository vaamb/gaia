from typing import Type

from websockets import ConnectionClosedOK

from gaia.hardware.abc import Dimmer, Hardware, Switch, WebSocketHardware


class WebSocketSwitch(Switch, WebSocketHardware):
    async def turn_on(self) -> bool:
        try:
            response = await self._send_msg_and_wait({"action": "turn_actuator", "data": "on"})
        except (ConnectionError, ConnectionClosedOK):
            self._logger.error("Could not connect to the device")
            return False
        else:
            if response["status"] != "success":
                base_msg = "Failed to turn on the switch"
                if "message" in response:
                    base_msg = f"{base_msg}. Error msg: `{response['message']}`."
                self._logger.error(base_msg)
                return False
            return True


    async def turn_off(self) -> bool:
        try:
            response = await self._send_msg_and_wait({"action": "turn_actuator", "data": "off"})
        except (ConnectionError, ConnectionClosedOK):
            self._logger.error("Could not connect to the device")
            return False
        else:
            if response["status"] != "success":
                base_msg = "Failed to turn off the switch"
                if "message" in response:
                    base_msg = f"{base_msg}. Error msg: `{response['message']}`."
                self._logger.error(base_msg)
                return False
            return True


class WebSocketDimmer(Dimmer, WebSocketHardware):
    async def set_pwm_level(self, level) -> bool:
        try:
            response = await self._send_msg_and_wait({"action": "set_level", "data": level})
        except (ConnectionError, ConnectionClosedOK):
            self._logger.error("Could not connect to the device")
            return False
        else:
            if response["status"] != "success":
                base_msg = f"Failed to set the level to {level}"
                if response.message:
                    base_msg = f"{base_msg}. Error msg: `{response['message']}`."
                self._logger.error(base_msg)
                return False
            return True


websocket_actuator_models: dict[str, Type[Hardware]] = {
    hardware.__name__: hardware
    for hardware in [
        WebSocketDimmer,
        WebSocketSwitch,
    ]
}
