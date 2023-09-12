from __future__ import annotations

import logging
from typing import Type

from gaia.config import GaiaConfig, get_config
from gaia.engine import Engine
from gaia.utils import configure_logging


def main():
    from setproctitle import setproctitle

    setproctitle("gaia")

    config_cls: Type[GaiaConfig] = get_config()
    configure_logging(config_cls)

    logger = logging.getLogger("gaia")
    logger.info("Initializing Gaia")
    gaia_engine = Engine()
    try:
        logger.info("Starting Gaia")
        gaia_engine.run()
        logger.info("GAIA started successfully")
    finally:
        logger.info("Stopping")
        gaia_engine.stop()
