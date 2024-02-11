from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import cast, TypedDict

from dispatcher import Dispatcher


class EmitDict(TypedDict):
    event: str
    data: dict | list | str | tuple | None
    room: str
    namespace: str



@contextmanager
def get_logs_content(logger_path: Path):
    with open(logger_path, "r+") as logger_handle:
        logs = logger_handle.read()
        yield logs
        logger_handle.truncate(0)


class MockDispatcher(Dispatcher):
    def __init__(self, namespace: str):
        super().__init__(namespace)
        self.emit_store: list[EmitDict] = []

    def emit(
            self,
            event: str,
            data: dict | list | str | tuple | None = None,
            to: dict | None = None,
            room: str | None = None,
            namespace: str | None = None,
            ttl: int | None = None,
            **kwargs
    ):
        self.emit_store.append(cast(EmitDict, {
            "event": event,
            "data": data,
            "room": room,
            "namespace": namespace,
        }))

    def clear_store(self):
        self.emit_store.clear()

    def start(self, *args, **kwargs) -> None:
        pass
