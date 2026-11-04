from __future__ import annotations

import asyncio
from asyncio import Event
from contextlib import suppress
from datetime import date, datetime, time
import hashlib
from pathlib import Path
from typing import Type, TypeVar

from anyio.to_thread import run_sync
import pydantic
from pydantic import RootModel

import gaia_validators as gv

from gaia.utils import get_yaml, json


# ---------------------------------------------------------------------------
#   General utility functions
# ---------------------------------------------------------------------------
async def load_json(path: Path) -> dict:
    def load_json_sync() -> dict:
        with open(path, "r") as file:
            data: dict = json.loads(file.read())
        return data

    return await run_sync(load_json_sync)


async def dump_json(data: dict, path: Path) -> None:
    def dump_json_sync() -> None:
        with open(path, "w") as file:
            file.write(json.dumps(data))

    return await run_sync(dump_json_sync)


async def load_yaml(path: Path) -> dict:
    yaml = get_yaml()

    def load_yaml_sync() -> dict:
        with open(path, "r") as file:
            data: dict = yaml.load(file)
        return data

    return await run_sync(load_yaml_sync)


async def dump_yaml(data: dict, path: Path) -> None:
    yaml = get_yaml()

    def dump_yaml_sync() -> None:
        with open(path, "w") as file:
            yaml.dump(data, file)

    await run_sync(dump_yaml_sync)


def file_checksum(file_path: Path, _buffer_size: int = 4096) -> bytes:
    try:
        with open(file_path, "rb") as file_obj:
            digest_obj = hashlib.md5(usedforsecurity=False)
            # coming from hashlib.file_digest
            buffer = bytearray(_buffer_size)
            view = memoryview(buffer)
            while True:
                size = file_obj.readinto(buffer)
                if size == 0:
                    break  # EOF
                digest_obj.update(view[:size])
            return digest_obj.digest()
    except FileNotFoundError:  # pragma: no cover
        return b"\x00"


def to_dt(_time: time) -> datetime:
    # Transforms time to today's datetime. Needed to use timedelta
    _date = date.today()
    return datetime.combine(_date, _time)


def remove_missing(to_clean: dict) -> dict:
    return {
        key: value
        for key, value in to_clean.items()
        if not gv.is_missing(value)
    }


async def event_wait(event: Event, timeout: float | int):
    # suppress TimeoutError because wait_for returns False in case of timeout
    with suppress(asyncio.TimeoutError):
        await asyncio.wait_for(event.wait(), timeout)
    return event.is_set()


# ---------------------------------------------------------------------------
#   Pydantic utility functions
# ---------------------------------------------------------------------------
def format_pydantic_error(error: pydantic.ValidationError) -> str:
    errors = error.errors()
    return ". ".join([
        f"{e['type'].replace('_', ' ').upper()} at parameter '{e['loc'][0]}', " \
        f"input '{e['input']}' is not valid."
        for e in errors
    ])


D = TypeVar("D", bound=dict)


def validate_from_root_model(
        unvalidated_data: D,
        root_model: Type[RootModel],
        *,
        exclude_defaults: bool = False,
) -> D:
    return (
        root_model
            .model_validate(unvalidated_data)
            .model_dump(exclude_defaults=exclude_defaults)
    )
