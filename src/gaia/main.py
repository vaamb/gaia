from __future__ import annotations

import logging
from time import sleep
from typing import Type

from gaia.config import GaiaConfig, get_config
from gaia.engine import Engine
from gaia.shared_resources import get_scheduler, start_scheduler
from gaia.utils import configure_logging


def main():
    from setproctitle import setproctitle

    setproctitle("gaia")

    gaia = Gaia()
    try:
        gaia.start()
        gaia.wait()
    finally:
        gaia.stop()


class Gaia:
    def __init__(
            self,
            config_cls: Type[GaiaConfig] = get_config(),
    ) -> None:
        configure_logging(config_cls)
        self._config: Type[GaiaConfig] = config_cls
        self.logger = logging.getLogger("gaia")
        self.logger.info("Initializing Gaia")
        self.started: bool = False
        self.engine = Engine()

    def start(self) -> None:
        if not self.started:
            self.logger.info("Starting Gaia")
            self.engine.start()
            self.started = True
            self.logger.info("GAIA started successfully")
        else:
            raise RuntimeError("Only one instance of gaiaEngine can be run")

    def wait(self):
        if self.started:
            self.logger.info("Running")
            while True:
                sleep(1)
        else:
            raise RuntimeError("Gaia needs to be started in order to wait")

    def stop(self):
        if self.started:
            self.logger.info("Stopping")
            self.engine.stop()
            self.started = False
