from __future__ import annotations

from asyncio import Event
from logging import getLogger, Logger
import typing as t

from websockets import basic_auth, serve, ServerConnection
from websockets.exceptions import ConnectionClosed


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia import EngineConfig


class WebSocketHardwareManager:
    def __init__(self, engine_config: EngineConfig):
        self.logger: Logger = getLogger("gaia.hardware.websocket")
        self._engine_config = engine_config
        self._port = engine_config.app_config.HARDWARE_WEBSOCKET_PORT
        password = engine_config.app_config.HARDWARE_WEBSOCKET_PASSWORD
        if password == "gaia" and engine_config.app_config.PRODUCTION:
            raise ValueError("Production build should not use `gaia` as a password")
        self._password: str = password
        self._registered_hardware: set[str] = set()
        self.device_connections: dict[str, ServerConnection] = {}
        self._stop_event: Event = Event()

    async def register_hardware(self, hardware_uid: str) -> None:
        self._registered_hardware.add(hardware_uid)

    async def unregister_hardware(self, hardware_uid: str) -> None:
        await self.device_connections[hardware_uid].close()
        self._registered_hardware.remove(hardware_uid)

    async def run(self) -> None:
        async with serve(
                self.connection_handler,
                "127.0.0.1",
                self._port,
                server_header=None,
                process_request=basic_auth(credentials=("gaia-device", self._password))
        ):
            await self._stop_event.wait()

    async def stop(self) -> None:
        self._stop_event.set()

    async def connection_handler(self, connection: ServerConnection) -> None:
        # We should receive the device uid first
        try:
            device_uid = await connection.recv(decode=True)
        except ConnectionClosed:
            return
        # If the device uid is registered, close the connection
        if device_uid not in self._registered_hardware:
            self.logger.warning(
                f"Device {device_uid} is trying to connect but is not in the "
                f"ecosystem config, closing connection")
            await connection.close()
            return
        # Store the connection for later retrieval
        self.logger.debug(f"Device {device_uid} connected")
        self.device_connections[device_uid] = connection
        # Keep the connection open and remove it from the dictionary when it is closed
        try:
            await self._stop_event.wait()
        except ConnectionClosed:
            self.logger.debug(f"Device {device_uid} disconnected")
            self.device_connections.pop(device_uid)

    async def get_connection(self, device_uid: str) -> ServerConnection | None:
        if not device_uid in self._registered_hardware:
            raise RuntimeError(f"Hardware {device_uid} was never registered")
        try:
            return self.device_connections[device_uid]
        except KeyError:
            return None
