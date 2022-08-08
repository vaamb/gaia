#!/usr/bin/python3
from setproctitle import setproctitle

setproctitle("Gaia")

import eventlet

eventlet.monkey_patch()

from config import Config
from src import Gaia
from src.utils import configure_logging


if __name__ == "__main__":
    if Config.DEBUG:
        Config.USE_DATABASE = True
        Config.USE_BROKER = True

    configure_logging(Config)
    gaia = Gaia(
        connect_to_ouranos=Config.USE_BROKER,
        use_database=Config.USE_DATABASE,
    )
    try:
        gaia.start()
        gaia.wait()
    finally:
        gaia.stop()
