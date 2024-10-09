from __future__ import annotations

import logging
import typing as t

from adafruit_platformdetect import Board, Detector


if t.TYPE_CHECKING:  # pragma: no cover
    from busio import I2C


_is_raspi: bool | None = None
_i2c: "I2C" | None = None


_store: dict[str, "I2C"] = {}


hardware_logger = logging.getLogger("gaia.hardware_store")


def is_raspi() -> bool:
    global _is_raspi
    if _is_raspi is None:
        _is_raspi = Board(Detector()).any_raspberry_pi
    return _is_raspi


def get_i2c() -> "I2C":
    global _i2c
    if _i2c is None:
        if is_raspi():
            import board
            import busio
        else:
            from gaia.hardware._compatibility import board, busio
        _i2c = busio.I2C(board.SCL, board.SDA)
    return _i2c
