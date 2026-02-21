from __future__ import annotations

from asyncio import sleep
from typing import AsyncIterator, TypedDict

from dispatcher import AsyncDispatcher


class EmitDict(TypedDict):
    event: str
    data: dict | list | str | tuple | None
    room: str
    namespace: str


async def yield_control(iterations: int = 10) -> None:
    """Yield control to the event loop to let background tasks process."""
    for _ in range(iterations):
        await sleep(0)


class MockDispatcher(AsyncDispatcher):
    def __init__(self, namespace: str):
        super().__init__(namespace)
        self.emit_store: list[EmitDict] = []

    async def _listen(self) -> AsyncIterator[bytes]:
        pass  # No used

    async def _publish(self, namespace: str, payload: bytes, ttl: int | None = None,
                       timeout: int | float | None = None) -> None:
        pass  # Short-circuited

    async def _broker_reachable(self) -> bool:
        return True

    async def emit(
            self,
            event: str,
            data: dict | list | str | tuple | None = None,
            to: dict | None = None,
            room: str | None = None,
            namespace: str | None = None,
            ttl: int | None = None,
            **kwargs,
    ):
        self.emit_store.append(
            EmitDict(**{
                "event": event,
                "data": data,
                "room": room,
                "namespace": namespace,
            })
        )

    def clear_store(self):
        self.emit_store.clear()

    async def start(self, *args, **kwargs) -> None:
        self._connected.set()
        self._running.set()

    async def stop(self, *args, **kwargs) -> None:
        self._running.set()
        self._connected.set()
