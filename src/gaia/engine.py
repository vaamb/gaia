from __future__ import annotations

from asyncio import get_event_loop, sleep, Task
from json.decoder import JSONDecodeError
import logging
import logging.config
import signal
import threading
from threading import Event
import typing as t
from typing import Type
import weakref

from gaia.config import (
    EngineConfig, GaiaConfig, get_cache_dir, get_config, get_ecosystem_IDs)
from gaia.config.from_files import detach_config
from gaia.ecosystem import Ecosystem
from gaia.exceptions import UndefinedParameter
from gaia.shared_resources import get_scheduler, start_scheduler
from gaia.utils import configure_logging, json, SingletonMeta
from gaia.virtual import get_virtual_ecosystem


if t.TYPE_CHECKING:
    from dispatcher import AsyncDispatcher
    from sqlalchemy_wrapper import AsyncSQLAlchemyWrapper

    from gaia.events import Events


SIGNALS = (
    signal.SIGINT,
    signal.SIGTERM,
)


class Engine(metaclass=SingletonMeta):
    """An Engine class that will coordinate several Ecosystem instances.

    Under normal circumstances only one Ecosystem instance should be created
    for each ecosystem. The Engine makes sure this is the case. It also
    manages the config watchdog and updates the sun times once a day.
    When used within Gaia, the Engine is automatically instantiated when needed.
    """
    def __init__(self) -> None:
        self._config: EngineConfig = EngineConfig()
        self._config.engine = self
        self.gaia_config: Type[GaiaConfig] = get_config()
        configure_logging(self.gaia_config)
        self.logger: logging.Logger = logging.getLogger(f"gaia.engine")
        self.logger.info("Initializing Gaia")
        self._ecosystems: dict[str, Ecosystem] = {}
        self._uid: str = self.gaia_config.ENGINE_UID
        self._message_broker: "AsyncDispatcher" | None = None
        self._event_handler: "Events" | None = None
        self._db: "AsyncSQLAlchemyWrapper" | None = None
        self.plugins_needed = (
            self.gaia_config.USE_DATABASE
            or self. gaia_config.COMMUNICATE_WITH_OURANOS
        )
        self.plugins_initialized: bool = False
        self._task: Task | None = None
        self._started_event: Event = Event()

    def __del__(self):
        self._config.engine = None

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._uid}, config={self.config})"

    # ---------------------------------------------------------------------------
    #   Events dispatcher
    # ---------------------------------------------------------------------------
    async def init_message_broker(self) -> None:
        broker_url = self.gaia_config.AGGREGATOR_COMMUNICATION_URL
        broker_type = broker_url[:broker_url.index("://")]
        if broker_type == "amqp":
            try:
                from dispatcher import AsyncAMQPDispatcher
            except ImportError:
                raise RuntimeError(
                    "Event-dispatcher is required to use the dispatcher. "
                    "Download it from `https://github.com/vaamb/dispatcher` and "
                    "install it in your virtual env"
                )
            if broker_url == "amqp://":
                broker_url = "amqp://guest:guest@localhost:5672//"
            self.message_broker = AsyncAMQPDispatcher(
                "gaia", url=broker_url, queue_options={
                    "name": f"gaia-{self.gaia_config.ENGINE_UID}",
                    "durable": True,
                    "arguments": {
                        # Delete the queue after one week, CRUD requests will be lost
                        #  at this point
                        "x-expires": 60 * 60 * 24 * 7 * 1000,
                    },
                })
        elif broker_type == "redis":
            try:
                from dispatcher import AsyncRedisDispatcher
            except ImportError:
                raise RuntimeError(
                    "Event-dispatcher is required to use the dispatcher. "
                    "Download it from `https://github.com/vaamb/dispatcher` and "
                    "install it in your virtual env"
                )
            if broker_url == "redis://":
                broker_url = "redis://localhost:6379/0"
            self.message_broker = AsyncRedisDispatcher(
                "gaia", url=broker_url, queue_options={
                    "name": f"gaia-{self.gaia_config.ENGINE_UID}",
                })
        else:
            raise ValueError(
                f"'{broker_type}' is not supported, it should be AMQP or redis")
        from gaia.events import Events
        events_handler = Events(engine=self)
        self.message_broker.register_event_handler(events_handler)
        self.event_handler = events_handler

    async def start_message_broker(self) -> None:
        self.logger.info("Starting the dispatcher")
        await self.message_broker.start(retry=True, block=False)

    async def stop_message_broker(self) -> None:
        self.logger.info("Stopping the dispatcher")
        await self.message_broker.stop()

    @property
    def message_broker(self) -> "AsyncDispatcher":
        if self._message_broker is None:
            raise AttributeError(
                "'message_broker' is not valid as the message broker between "
                "Ouranos and Gaia is not used. To use it, set the Gaia app config "
                "parameter 'COMMUNICATE_WITH_OURANOS' to True, and the "
                "parameter 'AGGREGATOR_COMMUNICATION_URL' to a valid url")
        return self._message_broker

    @message_broker.setter
    def message_broker(self, broker: "AsyncDispatcher" | None) -> None:
        self._message_broker = broker

    @property
    def use_message_broker(self) -> bool:
        return self._event_handler is not None

    @property
    def event_handler(self) -> "Events":
        """Return the event handler"""
        if self._event_handler is not None:
            return self._event_handler
        raise AttributeError("'event_handler' has not been set")

    @event_handler.setter
    def event_handler(self, event_handler: "Events"):
        self._event_handler = event_handler

    # ---------------------------------------------------------------------------
    #   DB
    # ---------------------------------------------------------------------------
    async def init_database(self) -> None:
        self.logger.info("Initialising the database")
        from gaia.database import db
        self.db = db
        self.db.init(self.gaia_config)
        await self.db.create_all()

    async def start_database(self) -> None:
        from gaia.database import routines
        if self.gaia_config.SENSORS_LOGGING_PERIOD:
            scheduler = get_scheduler()
            scheduler.add_job(
                routines.log_sensors_data,
                kwargs={"scoped_session_": self.db.scoped_session, "engine": self},
                trigger="cron", minute="*", misfire_grace_time=10,
                id="log_sensors_data")

    async def stop_database(self) -> None:
        scheduler = get_scheduler()
        scheduler.remove_job("log_sensors_data")

    @property
    def db(self) -> "AsyncSQLAlchemyWrapper":
        if self._db is None:
            raise AttributeError(
                "'db' is not valid as the database is currently not used. To use "
                "it, set the Gaia app config parameter 'USE_DATABASE' to True")
        return self._db

    @db.setter
    def db(self, value: "AsyncSQLAlchemyWrapper" | None) -> None:
        self._db = value

    @property
    def use_db(self) -> bool:
        return self._db is not None

    # ---------------------------------------------------------------------------
    #   Plugins management
    # ---------------------------------------------------------------------------
    async def init_plugins(self) -> None:
        if self.gaia_config.COMMUNICATE_WITH_OURANOS:
            await self.init_message_broker()
        if self.gaia_config.USE_DATABASE:
            await self.init_database()
        self.plugins_initialized = True

    async def start_plugins(self) -> None:
        if self.use_message_broker:
            await self.start_message_broker()
        if self.use_db:
            await self.start_database()

    async def stop_plugins(self) -> None:
        if self.use_message_broker:
            await self.stop_message_broker()
        if self.use_db:
            await self.stop_database()

    # ---------------------------------------------------------------------------
    #   Engine functionalities
    # ---------------------------------------------------------------------------
    def start_background_tasks(self) -> None:
        self.logger.debug("Starting background tasks")
        scheduler = get_scheduler()
        scheduler.add_job(self.refresh_sun_times, "cron",
                          hour="1", misfire_grace_time=15 * 60,
                          id="refresh_sun_times")
        scheduler.add_job(self.refresh_chaos, "cron",
                          hour="0", minute="5", misfire_grace_time=15 * 60,
                          id="refresh_chaos")
        start_scheduler()

    def stop_background_tasks(self) -> None:
        self.logger.debug("Stopping background tasks")
        scheduler = get_scheduler()
        scheduler.remove_job("refresh_sun_times")
        scheduler.remove_job("refresh_chaos")
        scheduler.remove_all_jobs()  # To be 100% sure
        scheduler.shutdown()

    def _init_virtualization(self) -> None:
        if self.gaia_config.VIRTUALIZATION:
            for ecosystem_uid in self.config.ecosystems_uid:
                get_virtual_ecosystem(ecosystem_uid, start=True)

    async def _loop(self) -> None:
        while True:
            async with self.config.new_config:
                await self.config.new_config.wait()
            if not self.started:
                break
            await self.refresh_ecosystems()
            if self.use_message_broker:
                await self.event_handler.send_full_config()
                await self.event_handler.send_light_data()

    """
    API calls
    """
    @property
    def uid(self) -> str:
        return self._uid

    @property
    def started(self) -> bool:
        return self._started_event.is_set()

    @property
    def ecosystems(self) -> dict[str, Ecosystem]:
        return self._ecosystems

    @property
    def config(self) -> EngineConfig:
        return self._config

    @property
    def ecosystems_started(self) -> set[str]:
        return set([
            ecosystem.uid for ecosystem in self.ecosystems.values()
            if ecosystem.status
        ])

    @property
    def task(self) -> Task:
        if self._task is None:
            raise AttributeError("Engine management task has not been set up")
        else:
            return self._task

    @task.setter
    def task(self, task: Task | None):
        self._task = task

    # ---------------------------------------------------------------------------
    #   Ecosystem managements
    # ---------------------------------------------------------------------------
    def init_ecosystem(self, ecosystem_id: str, start: bool = False) -> Ecosystem:
        """Initialize an Ecosystem

        :param ecosystem_id: The name or the uid of an ecosystem, as written in
                             'ecosystems.cfg'
        :param start: Whether to immediately start the ecosystem after its
                      creation or not
        """
        ecosystem_uid, ecosystem_name = get_ecosystem_IDs(ecosystem_id)
        if ecosystem_uid not in self.ecosystems:
            ecosystem = Ecosystem(ecosystem_uid, self)
            self.ecosystems[ecosystem_uid] = ecosystem
            self.logger.debug(
                f"Ecosystem {ecosystem_name} has been created"
            )
            if start:
                self.start_ecosystem(ecosystem_uid)
            return ecosystem
        raise RuntimeError(
            f"Ecosystem {ecosystem_id} already exists"
        )

    async def start_ecosystem(self, ecosystem_id: str) -> None:
        """Start an Ecosystem

        :param ecosystem_id: The name or the uid of an ecosystem, as written in
                             'ecosystems.cfg'
        """
        ecosystem_uid, ecosystem_name = get_ecosystem_IDs(ecosystem_id)
        if ecosystem_uid in self.ecosystems:
            if ecosystem_uid not in self.ecosystems_started:
                ecosystem: Ecosystem = self.ecosystems[ecosystem_uid]
                self.logger.debug(
                    f"Starting ecosystem {ecosystem_name}"
                )
                await ecosystem.start()
            else:
                raise RuntimeError(
                    f"Ecosystem {ecosystem_id} is already running"
                )
        else:
            raise RuntimeError(
                f"Neet to initialise Ecosystem {ecosystem_id} first"
            )

    async def stop_ecosystem(self, ecosystem_id: str, dismount: bool = False) -> None:
        """Stop an Ecosystem

        :param ecosystem_id: The name or the uid of an ecosystem, as written in
                             'ecosystems.cfg'
        :param dismount: Whether to remove the Ecosystem from the memory or not.
                         If dismounted, the Ecosystem will need to be recreated
                         before being able to restart.
        """
        ecosystem_uid, ecosystem_name = get_ecosystem_IDs(ecosystem_id)
        if ecosystem_uid in self.ecosystems:
            if ecosystem_uid in self.ecosystems_started:
                ecosystem = self.ecosystems[ecosystem_uid]
                await ecosystem.stop()
                if dismount:
                    self.dismount_ecosystem(ecosystem_uid)
                self.logger.info(
                    f"Ecosystem {ecosystem_name} has been stopped")
        else:
            raise RuntimeError(
                f"Cannot stop Ecosystem {ecosystem_id} as it has not been "
                f"initialised"
            )

    def dismount_ecosystem(
            self,
            ecosystem_id: str,
            detach_config_: bool = True
    ) -> None:
        """Remove the Ecosystem from Engine's memory

        :param ecosystem_id: The name or the uid of an ecosystem, as written in
                             'ecosystems.cfg'
        :param detach_config_: Whether to remove the Ecosystem's config from
                               memory or not.
        """
        ecosystem_id, ecosystem_name = get_ecosystem_IDs(ecosystem_id)
        if ecosystem_id in self.ecosystems:
            if ecosystem_id in self.ecosystems_started:
                raise RuntimeError(
                    "Cannot dismount a started Ecosystem. First stop it"
                )
            else:
                del self.ecosystems[ecosystem_id]
                if detach_config_:
                    detach_config(ecosystem_id)
                self.logger.info(
                    f"Ecosystem '{ecosystem_id}' has been dismounted"
                )
        else:
            raise RuntimeError(
                f"Cannot dismount Ecosystem '{ecosystem_id}' as it has not been "
                f"initialised"
            )

    def get_ecosystem(self, ecosystem: str) -> Ecosystem:
        """Get the required Ecosystem

        :param ecosystem: The name or the uid of an ecosystem, as written in
                          'ecosystems.cfg'
        """
        ecosystem_uid, ecosystem_name = get_ecosystem_IDs(ecosystem)
        if ecosystem_uid in self.ecosystems:
            _ecosystem = self.ecosystems[ecosystem_uid]
        else:
            _ecosystem = self.init_ecosystem(ecosystem_uid)
        return _ecosystem

    async def refresh_ecosystems(self):
        """Starts and stops the Ecosystem based on the 'ecosystem.cfg' file"""
        expected_started = set(self.config.get_ecosystems_expected_to_run())
        to_delete = set(self.ecosystems.keys())
        for ecosystem_uid in self.config.ecosystems_uid:
            # create the Ecosystem if it doesn't exist
            if ecosystem_uid not in self.ecosystems:
                self.init_ecosystem(ecosystem_uid)
            # remove the Ecosystem from the to_delete set
            try:
                to_delete.remove(ecosystem_uid)
            except KeyError:
                pass
        # start Ecosystems which are expected to run and are not running
        to_start = expected_started - self.ecosystems_started
        for ecosystem_uid in to_start:
            await self.start_ecosystem(ecosystem_uid)
        # stop Ecosystems which are not expected to run and are currently
        # running
        to_stop = self.ecosystems_started - expected_started
        for ecosystem_uid in to_stop:
            await self.stop_ecosystem(ecosystem_uid)
        # refresh Ecosystems that were already running and did not stop
        started_before = self.ecosystems_started - to_start
        for ecosystem_uid in started_before:
            self.ecosystems[ecosystem_uid].refresh_subroutines()
        # delete Ecosystems which were created and are no longer on the
        # config file
        for ecosystem_uid in to_delete:
            await self.stop_ecosystem(ecosystem_uid)
            self.dismount_ecosystem(ecosystem_uid)

    async def refresh_sun_times(self) -> None:
        """Download sunrise and sunset times if needed by an Ecosystem"""
        self.logger.debug("Refreshing sun times")
        await self.config.refresh_sun_times()
        if self.config.sun_times is None:
            return
        need_refresh = []
        for ecosystem in self.ecosystems:
            try:
                if (
                    self.ecosystems[ecosystem].config.light_method.value in
                    ("mimic", "elongate")
                    # And expected to be running
                    and self.ecosystems[ecosystem].config.status
                ):
                    need_refresh.append(ecosystem)
            except UndefinedParameter:
                # Bad configuration file
                pass
        for ecosystem in need_refresh:
            try:
                if self.ecosystems[ecosystem].status:
                    await self.ecosystems[ecosystem].refresh_lighting_hours()
            except KeyError:
                # Occur
                pass

    def refresh_chaos(self) -> None:
        for ecosystem in self.ecosystems.values():
            ecosystem.refresh_chaos()
        chaos_file = get_cache_dir()/"chaos.json"
        try:
            with chaos_file.open("r+") as file:
                ecosystem_chaos = json.loads(file.read())
                ecosystems = [*ecosystem_chaos.keys()]
                for ecosystem in ecosystems:
                    if ecosystem not in self.ecosystems:
                        del ecosystem_chaos[ecosystem]
                file.write(json.dumps(ecosystem_chaos))
        except (FileNotFoundError, JSONDecodeError):  # Empty or absent file
            pass

    # ---------------------------------------------------------------------------
    #   Engine start and stop
    # ---------------------------------------------------------------------------
    async def startup(self) -> None:
        """Start the Engine

        When started, the Engine will automatically manage the Ecosystems based
        on the 'ecosystem.cfg' file and refresh the Ecosystems when changes are
        made in the file.
        """
        if self.started:  # pragma: no cover
            raise RuntimeError("Engine can only be started once")
        self.logger.info("Starting Gaia ...")
        if self.plugins_needed and not self.plugins_initialized:
            raise RuntimeError(
                "Plugins are needed but have not been initialized. Please use "
                "the 'init_plugins()' method to start them."
            )
        # Load the ecosystem configs into memory and start the watchdog
        await self.config.initialize_configs()
        self._init_virtualization()
        self.config.start_watchdog()
        # Get the info required just before starting the ecosystems
        await self.refresh_sun_times()
        # Start background tasks and plugins
        self.start_background_tasks()
        await self.start_plugins()
        self.task = Task(
            self._loop(),
            name="engine")
        await self.refresh_ecosystems()
        self._started_event.set()
        self.logger.info("Gaia started")

    async def shutdown(self) -> None:
        """Shutdown the Engine"""
        if not self.started:
            raise RuntimeError("Cannot shutdown a non-started Engine")
        self.logger.info("Stopping Gaia ...")
        # Send a config signal so the loops unlocks
        self._started_event.clear()
        async with self.config.new_config:
            self.config.new_config.notify_all()
        self.task.cancel()
        self.task = None
        # Stop and dismount ecosystems
        for ecosystem_uid in [*self.ecosystems_started]:
            await self.stop_ecosystem(ecosystem_uid)
        to_delete = [*self.ecosystems.keys()]
        for ecosystem in to_delete:
            self.dismount_ecosystem(ecosystem)
        # Stop plugins and background tasks
        await self.stop_plugins()
        self.stop_background_tasks()
        self.logger.info("Gaia has stopped")

    async def wait(self):
        if self.started:
            self.logger.info("Running")
            while self.started:
                await sleep(0.5)
        else:
            raise RuntimeError("Gaia needs to be started in order to wait")

    def stop(self) -> None:
        if not self.started:
            raise RuntimeError("Cannot shutdown a non-started Engine")
        self.logger.info("Received a stop signal")
        self._started_event.clear()

    def add_signal_handler(self) -> None:
        assert threading.current_thread() is threading.main_thread()

        def signal_handler(signum, frame) -> None:
            self.stop()

        loop = get_event_loop()

        try:
            for sig in SIGNALS:
                loop.add_signal_handler(sig, signal_handler, sig, None)
        except NotImplementedError:
            for sig in SIGNALS:
                signal.signal(sig, signal_handler)

    async def run(self) -> None:
        self.add_signal_handler()
        await self.startup()
        await self.wait()
        await self.shutdown()
