from __future__ import annotations

import logging
import time
from time import sleep
import typing as t
from typing import Type

from gaia.config import GaiaConfig, get_config
from gaia.engine import Engine
from gaia.shared_resources import scheduler, start_scheduler
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
        sleep(10)
        #gaia.wait()
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
        self._message_broker: "KombuDispatcher" | None = None
        self._db: "SQLAlchemyWrapper" | None = None
        if self._config.USE_DATABASE:
            self._init_database()
        if self._config.COMMUNICATE_WITH_OURANOS:
            broker_url = config_cls.AGGREGATOR_COMMUNICATION_URL
            broker_type = broker_url[:broker_url.index("://")]
            if broker_type not in {"amqp", "redis"}:
                raise ValueError(f"{broker_type} is not supported")
            self._init_message_broker()

    def _init_message_broker(self) -> None:
        self.logger.info("Initialising the message broker")
        broker_url = self._config.AGGREGATOR_COMMUNICATION_URL
        self.logger.debug("Initializing the dispatcher")
        if broker_url == "amqp://":
            broker_url = "amqp://guest:guest@localhost:5672//"
        elif broker_url == "redis://":
            broker_url = "redis://localhost:6379/0"
        try:
            from dispatcher import KombuDispatcher
        except ImportError:
            raise RuntimeError(
                "Event-dispatcher is required to use the dispatcher. Download it "
                "from `https://github.com/vaamb/dispatcher` and install it in "
                "your virtual env"
            )
        self.message_broker = KombuDispatcher(
            "gaia", url=broker_url, queue_options={
                "name": f"gaia-{self._config.ENGINE_UID}",
                # Delete the queue after one week, CRUD requests will be lost
                #  at this point
                "expires": 60 * 60 * 24 * 7
            })
        from gaia.events import Events
        events_handler = Events(engine=self.engine)
        self.message_broker.register_event_handler(events_handler)
        self.engine.event_handler = events_handler

    def _start_message_broker(self) -> None:
        self.message_broker: "KombuDispatcher"
        self.logger.info("Starting the dispatcher")
        self.message_broker.start(retry=True, block=False)

    def _init_database(self) -> None:
        self.logger.info("Initialising the database")
        from gaia.database import routines, db
        self.db = db
        self.db.init(get_config())
        self.db.create_all()
        if get_config().SENSORS_LOGGING_PERIOD:
            scheduler.add_job(
                routines.log_sensors_data,
                kwargs={"scoped_session": self.db.scoped_session, "engine": self.engine},
                trigger="cron", minute="*", misfire_grace_time=10,
                id="log_sensors_data")

    @property
    def message_broker(self) -> "KombuDispatcher":
        if self._message_broker is None:
            raise RuntimeError(
                "'message_broker' is not valid as the message broker between "
                "Ouranos and Gaia is not used. To use it, set the config "
                "parameter 'COMMUNICATE_WITH_OURANOS' to True, and the "
                "parameter 'AGGREGATOR_COMMUNICATION_URL' to a valid url")
        return self._message_broker

    @message_broker.setter
    def message_broker(self, value: "KombuDispatcher" | None) -> None:
        self._message_broker = value

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

    def start(self) -> None:
        if not self.started:
            self.logger.info("Starting Gaia")
            start_scheduler()
            self.engine.start()
            if self._config.COMMUNICATE_WITH_OURANOS:
                self._start_message_broker()
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
            if self._config.COMMUNICATE_WITH_OURANOS:
                self.message_broker.stop()
            self.started = False
