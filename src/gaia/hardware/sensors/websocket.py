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
            data = await self._send_msg_and_wait({"action": "send_data"})
            self._logger.error("Could not connect to the device")
        except (ConnectionError, ConnectionClosed):
            return []
        try:
            data: list[gv.SensorRecord] = SensorRecords.model_validate(data).model_dump()
        except ValidationError:
            self._logger.error(f"Received an invalid `SensorRecord`: {data}")
            return []
        else:
            return data


websocket_sensor_models: dict[str, Type[BaseSensor]] = {
    hardware.__name__: hardware
    for hardware in [
        WebSocketSensor,
    ]
}
