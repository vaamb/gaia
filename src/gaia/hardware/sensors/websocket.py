from typing import Type

from pydantic import RootModel, ValidationError

import gaia_validators as gv

from gaia.hardware.abc import BaseSensor, WebSocketHardware

from websockets import ConnectionClosed


SensorRecords = RootModel[list[gv.SensorRecord]]


class WebSocketSensor(BaseSensor, WebSocketHardware):
    measures_available = ...

    async def get_data(self) -> list[gv.SensorRecord]:
        try:
            response = await self._send_msg_and_wait({"action": "send_data"})
        except (ConnectionError, ConnectionClosed, TimeoutError) as e:
            self._logger.error(f"Could not connect: {e}")
            return []
        try:
            data = response["data"]
            data: list[gv.SensorRecord] = SensorRecords.model_validate(data).model_dump()
        except (KeyError, ValidationError):
            self._logger.error(f"Received an invalid response: {response}")
            return []
        else:
            return data


websocket_sensor_models: dict[str, Type[BaseSensor]] = {
    hardware.__name__: hardware
    for hardware in [
        WebSocketSensor,
    ]
}
