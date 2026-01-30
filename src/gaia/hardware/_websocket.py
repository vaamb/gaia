from __future__ import annotations

from asyncio import create_task, Event, sleep, Task
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
        self._registered_hardware: dict[str, str] = {}
        self.device_connections: dict[str, ServerConnection] = {}
        self._running_task: Task | None = None
        self._stop_event: Event = Event()

    @property
    def is_running(self) -> bool:
        return self._running_task is not None

    async def register_hardware(self, hardware_uid: str, remote_ip: str | None = None) -> None:
        self._registered_hardware[hardware_uid] = remote_ip

    async def unregister_hardware(self, hardware_uid: str) -> None:
        if hardware_uid in self.device_connections:
            await self.device_connections[hardware_uid].close()
        self._registered_hardware.pop(hardware_uid, None)

    @property
    def registered_hardware(self) -> int:
        return len(self._registered_hardware)

    async def _start(self) -> None:
        async with serve(
                self.connection_handler,
                "127.0.0.1",
                self._port,
                server_header=None,
                process_request=basic_auth(credentials=("gaia-device", self._password))
        ):
            await self._stop_event.wait()

    async def start(self) -> None:
        if self.is_running:
            raise RuntimeError("WebSocketHardwareManager is already running")
        self._running_task = create_task(self._start())
        await sleep(0.1)  # Allow the task to start

    async def stop(self) -> None:
        if not self.is_running:
            raise RuntimeError("WebSocketHardwareManager is not currently running")
        self._stop_event.set()
        self._running_task.cancel()
        self._running_task = None

    async def connection_handler(self, connection: ServerConnection) -> None:
        # We should receive the device uid first
        try:
            device_uid = await connection.recv(decode=True)
        except ConnectionClosed:
            return
        # If the device uid is registered, close the connection
        if device_uid not in self._registered_hardware:
            self.logger.warning(
                f"Device {device_uid} is trying to connect but is not registered. "
                f"Closing connection")
            await connection.close()
            return
        expected_ip = self._registered_hardware[device_uid]
        if (
                expected_ip is not None
                and connection.remote_address[0] != expected_ip
        ):
            self.logger.warning(
                f"Device {device_uid} is trying to connect from an unexpected "
                f"address, closing connection")
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

    def get_connection(self, device_uid: str) -> ServerConnection | None:
        if device_uid not in self._registered_hardware:
            raise ConnectionError(f"Hardware {device_uid} was never registered")
        try:
            return self.device_connections[device_uid]
        except KeyError:
            return None
