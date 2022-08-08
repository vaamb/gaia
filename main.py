#!/usr/bin/python3
from setproctitle import getproctitle, setproctitle
#!/usr/bin/python3
from setproctitle import setproctitle

setproctitle("Gaia")

import eventlet

eventlet.monkey_patch()

import logging

import psutil

from config import Config
from src import Gaia
from src.utils import configure_logging


if __name__ == "__main__":
    configure_logging(Config)
    logger = logging.getLogger("gaia")
    STARTED = False
    for process in psutil.process_iter():
        if "gaia" in process.name().lower():
            STARTED = True
    if STARTED:
        logger.error("Only one instance of Gaia should be running at the time")
    else:
        if Config.DEBUG:
            logger.info("Using debugging config")
            Config.USE_DATABASE = True
            Config.USE_BROKER = True

        gaia = Gaia(
            connect_to_ouranos=Config.USE_BROKER,
            use_database=Config.USE_DATABASE,
        )
        try:
            gaia.start()
            gaia.wait()
        finally:
            gaia.stop()
