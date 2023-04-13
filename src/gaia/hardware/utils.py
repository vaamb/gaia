from typing import Any

from adafruit_platformdetect import Board, Detector

_IS_RASPI: bool = Board(Detector()).any_raspberry_pi  # noqa


_store: dict[str, Any] = {}


def get_i2c():
    try:
        return _store["I2C"]
    except KeyError:
        if _IS_RASPI:
            try:
                from adafruit_blinka import board, busio
            except ImportError:
                raise RuntimeError(
                    "Adafruit blinka package is required. Run `pip install "
                    "adafruit-blinka` in your virtual env`."
                )
        else:
            from gaia.hardware._compatibility import board, busio
        _store["I2C"] = busio.I2C(board.SCL, board.SDA)
        return _store["I2C"]
