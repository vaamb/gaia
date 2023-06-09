from __future__ import annotations

import logging
from threading import Thread
from time import sleep
import typing as t
from typing import Type

from gaia.config import GaiaConfig, EngineConfig, get_config
from gaia.engine import Engine
from gaia.shared_resources import scheduler, start_scheduler
from gaia.utils import configure_logging, json


if t.TYPE_CHECKING:
    from dispatcher import KombuDispatcher

    from sqlalchemy_wrapper import SQLAlchemyWrapper

    from gaia.events.sio_based_handler import RetryClient


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
        self.logger = logging.getLogger("gaia")
        self.logger.info("Initializing Gaia")
        self.connect_to_ouranos: bool = config_cls.COMMUNICATE_WITH_OURANOS
        self.use_database = config_cls.USE_DATABASE
        self._thread: Thread | None = None
        self.engine = Engine()
        self._broker_url = config_cls.AGGREGATOR_COMMUNICATION_URL
        self.message_broker: "KombuDispatcher" | "RetryClient" | None = None
        self.db: "SQLAlchemyWrapper" | None = None
        if self.use_database:
            self._init_database()
        if self.connect_to_ouranos:
            self._init_message_broker()
        self.started: bool = False

    def _init_message_broker(self) -> None:
        self.logger.info("Initialising the message broker")
        broker_type = self._broker_url[:self._broker_url.index("://")]
        if broker_type == "socketio":
            self.logger.debug("Initializing the SocketIO client")
            from gaia.events.sio_based_handler import SioBasedGaiaEvents, RetryClient
            self.message_broker = RetryClient(json=json, logger=get_config().DEBUG)
            namespace = SioBasedGaiaEvents(
                namespace="/gaia", engine=self.engine)
            self.message_broker.register_namespace(namespace)
            events_handler = self.message_broker.namespace_handlers["/gaia"]

        elif broker_type in {"amqp", "redis"}:
            self.logger.debug("Initializing the dispatcher")
            from dispatcher import KombuDispatcher
            from gaia.events.dispatcher_based_handler import DispatcherBasedGaiaEvents
            self.message_broker = KombuDispatcher(
                "gaia", url=self._broker_url, queue_options={
                    "name": f"gaia-{get_config().ENGINE_UID}", "durable": True
                }
            )
            events_handler = DispatcherBasedGaiaEvents(
                namespace="aggregator", engine=self.engine)
            self.message_broker.register_event_handler(events_handler)

        else:
            raise RuntimeError(
                f"{broker_type} is not supported"
            )

        self.engine.event_handler = events_handler

    def _connect_to_ouranos(self) -> None:
        if self.message_broker is not None:
            if hasattr(self.message_broker, "is_socketio"):
                self.message_broker: "RetryClient"
                self.logger.info("Starting socketIO client")

                def thread_func():
                    server_url = (
                        f"http:/"
                        f"{self._broker_url[self._broker_url.index('://'):]}"
                    )
                    self.message_broker.connect(
                        server_url, transports="websocket", namespaces=['/gaia']
                    )
                self.thread = Thread(target=thread_func)
                self.thread.name = "socketio.connection"
                self.thread.start()
            else:
                self.logger.info("Starting the dispatcher")
                self.message_broker.start()

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
                id="log_sensors_data",
            )

    @property
    def thread(self) -> Thread:
        if self._thread is None:
            raise RuntimeError("Thread has not been set up")
        else:
            return self._thread

    @thread.setter
    def thread(self, thread: Thread | None):
        self._thread = thread

    def start(self) -> None:
        if not self.started:
            self.logger.info("Starting Gaia")
            self.engine.start()
            if self.connect_to_ouranos:
                self._connect_to_ouranos()
            start_scheduler()
            self.started = True
            self.logger.info("GAIA started successfully")
        else:
            raise RuntimeError("Only one instance of gaiaEngine can be run")

    def wait(self):
        if self.started:
            self.logger.info("Running")
            while True:
                sleep(1)

    def stop(self):
        if self.started:
            self.logger.info("Stopping")
            self.engine.stop()
            if self.connect_to_ouranos:
                if hasattr(self.message_broker, "is_socketio"):
                    self.message_broker.disconnect()
                else:
                    self.message_broker.stop()
            self.started = False
