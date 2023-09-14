from __future__ import annotations

import logging

from gaia import Engine


def main():
    from setproctitle import setproctitle

    setproctitle("gaia")

    logger = logging.getLogger("gaia")
    logger.info("Initializing Gaia")
    gaia_engine = Engine()
    try:
        gaia_engine.init_plugins()
        logger.info("Starting Gaia")
        gaia_engine.run()
        logger.info("GAIA started successfully")
    finally:
        logger.info("Stopping")
        gaia_engine.stop()
