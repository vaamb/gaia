from asyncio import Event
from logging import getLogger, Logger

from websockets import basic_auth, serve, ServerConnection
from websockets.exceptions import ConnectionClosed

from gaia import Ecosystem


class WebSocketHardwareManager:
    def __init__(self, ecosystem: Ecosystem):
        self.logger: Logger = getLogger("gaia.hardware.websocket")
        self._ecosystem = ecosystem
        self._port = ecosystem.engine.config.app_config.HARDWARE_WEBSOCKET_PORT
        password = ecosystem.engine.config.app_config.HARDWARE_WEBSOCKET_PASSWORD
        if password == "gaia" and ecosystem.engine.config.app_config.PRODUCTION:
            raise ValueError("Production build should not use `gaia` as a password")
        self._password: str = password
        self.device_connections: dict[str, ServerConnection] = {}
        self._stop_event: Event = Event()

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
        # If the device uid is not in the ecosystem, close the connection
        if device_uid not in self._ecosystem.config.IO_dict:
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
        try:
            return self.device_connections[device_uid]
        except KeyError:
            return None
