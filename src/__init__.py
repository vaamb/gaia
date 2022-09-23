import itertools
import sys
import threading
from time import sleep
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from config import Config
from src.config_parser import GeneralConfig
from src.engine import Engine
from src.utils import json


try:
    url = Config.MESSAGE_BROKER_URL or "socketio://127.0.0.1:5000"
except AttributeError:
    url = "socketio://127.0.0.1:5000"
server = url[:url.index("://")]

_KOMBU_SUPPORTED = (
    "amqp", "amqps", "pyamqp", "librabbitmq", "memory", "redis", "rediss",
    "SQS", "sqs", "mongodb", "zookeeper", "sqlalchemy", "sqla", "SLMQ", "slmq",
    "filesystem", "qpid", "sentinel", "consul", "etcd", "azurestoragequeues",
    "azureservicebus", "pyro"
)

scheduler = BackgroundScheduler()

spinner = itertools.cycle(["", ".", "..", "..."])


class Gaia:
    def __init__(
            self,
            connect_to_ouranos: bool = False,
            use_database: bool = False,  # TODO
    ) -> None:
        self.logger = logging.getLogger("gaia")
        self.logger.info("Initializing Gaia")
        self.connect_to_ouranos = connect_to_ouranos
        self.use_database = use_database
        self.engine = Engine(GeneralConfig())
        self.message_broker = None
        if self.connect_to_ouranos:
            self._init_message_broker()
        self.db = None
        if self.use_database:
            self._init_database()
        self.started = False

    def _init_message_broker(self) -> None:
        def try_func(func):
            try:
                func()
            except Exception as e:
                log_msg = (
                    f"Encountered an error while sending light data. "
                    f"ERROR msg: `{e.__class__.__name__} :{e}`"
                )
                ex_msg = e.args[1] if len(e.args) > 1 else e.args[0]
                # If socketio error when not connected, log to debug
                if "is not a connected namespace" in ex_msg:
                    self.logger.debug(log_msg)
                else:
                    self.logger.error(log_msg)

        self.logger.info("Initialising the message broker")

        if server == "socketio":
            from .events.socketio import gaiaNamespace, RetryClient
            self.message_broker = RetryClient(json=json, logger=Config.DEBUG)
            namespace = gaiaNamespace(
                ecosystem_dict=self.engine.ecosystems, namespace="/gaia"
            )
            self.message_broker.register_namespace(namespace)
            events_handler = self.message_broker.namespace_handlers["/gaia"]

        elif server in ("amqp", "redis"):
            from dispatcher import KombuDispatcher
            from .events.dispatcher import gaiaNamespace
            self.logger.info("Starting dispatcher")
            self.message_broker = KombuDispatcher(
                "gaia", url=url,
                queue_options={"auto_delete": True, "durable": False}
            )
            events_handler = gaiaNamespace("aggregator", self.engine.ecosystems)
            self.message_broker.register_event_handler(events_handler)

        else:
            raise RuntimeError(
                f"{server} is not supported"
            )

        self.engine.event_handler = events_handler
        # Schedule jobs
        scheduler.add_job(
            try_func, kwargs={"func": events_handler.send_sensors_data},
            id="send_sensors_data", trigger="cron", minute="*",
            misfire_grace_time=10
        )
        scheduler.add_job(
            try_func, kwargs={"func": events_handler.send_light_data},
            id="send_light_data", trigger="cron", hour="1",
            misfire_grace_time=10
        )
        scheduler.add_job(
            try_func, kwargs={"func": events_handler.send_health_data},
            id="send_health_data", trigger="cron", hour="1",
            misfire_grace_time=10
        )

    def _connect_to_ouranos(self) -> None:
        if hasattr(self.message_broker, "is_socketio"):
            def thread_func():
                self.logger.info("Starting socketIO client")
                server_url = f"http{url[url.index('://'):]}"
                self.message_broker.connect(
                    server_url, transports="websocket", namespaces=['/gaia']
                )
            self._thread = threading.Thread(target=thread_func)
            self._thread.name = "socketio.connection"
            self._thread.start()
        else:
            self.message_broker.start()

    def _init_database(self) -> None:
        self.logger.info("Initialising the database")
        from .database import models, routines, SQLAlchemyWrapper
        self.db = SQLAlchemyWrapper(Config)
        self.db.create_all()
        scheduler.add_job(
            routines.log_sensors_data,
            kwargs={"scoped_session": self.db.scoped_session, "engine": self.engine},
            id="log_sensors_data", trigger="cron", minute="*",
            misfire_grace_time=10
        )

    def start(self) -> None:
        if not self.started:
            self.logger.info("Starting Gaia")
            self.engine.start()
            if self.connect_to_ouranos:
                self._connect_to_ouranos()
            scheduler.start()
            self.started = True
            self.logger.info("GAIA started successfully")
        else:
            raise RuntimeError("Only one instance of gaiaEngine can be run")

    def wait(self):
        if self.started:
            self.logger.info("Running")
            while True:
                sys.stdout.write("\r")
                sys.stdout.write(next(spinner))
                sys.stdout.write("\033[K")
                sys.stdout.flush()
                if hasattr(self.message_broker, "is_socketio"):
                    self.message_broker.sleep(1)
                else:
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
