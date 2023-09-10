from __future__ import annotations

import logging
from time import sleep
import typing as t
from typing import Type

from gaia.config import GaiaConfig, get_config
from gaia.engine import Engine
from gaia.shared_resources import get_scheduler, start_scheduler
from gaia.utils import configure_logging


if t.TYPE_CHECKING:
    from dispatcher import KombuDispatcher
    from sqlalchemy_wrapper import SQLAlchemyWrapper


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
        self._db: "SQLAlchemyWrapper" | None = None
        if self._config.USE_DATABASE:
            self._init_database()

    def _init_database(self) -> None:
        self.logger.info("Initialising the database")
        from gaia.database import routines, db
        self.db = db
        self.db.init(get_config())
        self.db.create_all()
        if get_config().SENSORS_LOGGING_PERIOD:
            scheduler = get_scheduler()
            scheduler.add_job(
                routines.log_sensors_data,
                kwargs={"scoped_session": self.db.scoped_session, "engine": self.engine},
                trigger="cron", minute="*", misfire_grace_time=10,
                id="log_sensors_data")

    @property
    def db(self) -> "SQLAlchemyWrapper":
        if self._db is None:
            raise RuntimeError(
                "'db' is not valid as the database is currently not used. To use "
                "it, set the config parameter 'USE_DATABASE' to True")
        return self._db

    @db.setter
    def db(self, value: "SQLAlchemyWrapper" | None) -> None:
        self._db = value

    @property
    def use_db(self) -> bool:
        return self._db is not None

    def start(self) -> None:
        if not self.started:
            self.logger.info("Starting Gaia")
            start_scheduler()
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
            scheduler = get_scheduler()
            if self.use_db:
                scheduler.remove_job("log_sensors_data")
            scheduler.remove_all_jobs()
            scheduler.shutdown()
            self.started = False
