from __future__ import annotations

import asyncio
from asyncio import Event, sleep, Task
import logging
import logging.config
from math import ceil
import signal
import threading
import typing as t
import warnings

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import gaia_validators as gv

from gaia.config.from_files import CacheType, EngineConfig
from gaia.ecosystem import Ecosystem
from gaia.utils import humanize_list, SingletonMeta
from gaia.virtual import VirtualWorld


if t.TYPE_CHECKING:  # pragma: no cover
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
    def __init__(self, engine_config: EngineConfig | None = None) -> None:
        self._config: EngineConfig = engine_config or EngineConfig()
        self.config.engine = self
        self.logger: logging.Logger = logging.getLogger("gaia.engine")
        self.logger.info("Initializing Gaia.")
        self._ecosystems: dict[str, Ecosystem] = {}
        self._uid: str = self.config.app_config.ENGINE_UID
        self._virtual_world: VirtualWorld | None = None
        self._scheduler: AsyncIOScheduler = AsyncIOScheduler()
        if self.config.app_config.VIRTUALIZATION:
            self.logger.info("Using ecosystem virtualization.")
            virtual_cfg = self.config.app_config.VIRTUALIZATION_PARAMETERS
            virtual_world_cfg: dict = virtual_cfg.get("world", {})
            self._virtual_world = VirtualWorld(self, **virtual_world_cfg)
        self._message_broker: AsyncDispatcher | None = None
        self._event_handler: Events | None = None
        self._db: AsyncSQLAlchemyWrapper | None = None
        self._db_started: bool = False
        self.plugins_initialized: bool = False
        self._task: Task | None = None
        self._running_event = Event()
        self._stop_event = Event()
        self._shut_down: bool = False

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.__class__.__name__}({self._uid}, config={self.config})"

    async def _async_init(self) -> None:
        if self.plugins_needed:
            # Initialize the database and the message broker
            await self.init_plugins()
        for ecosystem_uid in self.config.ecosystems_uid:
            await self.add_ecosystem(ecosystem_uid)

    @classmethod
    async def initialize(
            cls,
            engine_config: EngineConfig | None = None,
    ) -> t.Self:
        # Sync initialization of the ecosystem
        engine = cls(engine_config)
        # Finalization of the initialization
        await engine._async_init()
        return engine

    async def terminate(self) -> None:
        # Terminate the ecosystems first
        await self.terminate_ecosystems()
        # Stop plugins and background tasks
        if self.plugins_initialized:
            await self.stop_plugins()
        self._db = None
        self._message_broker = None

    @property
    def plugins_needed(self) -> bool:
        return (
            self.config.app_config.USE_DATABASE
            or self.config.app_config.COMMUNICATE_WITH_OURANOS
        )

    @property
    def virtual_world(self) -> VirtualWorld:
        if self._virtual_world is None:
            raise AttributeError(
                "'VIRTUALIZATION' needs to be set in GaiaConfig to use virtualization.")
        return self._virtual_world

    @property
    def scheduler(self) -> AsyncIOScheduler:
        return self._scheduler

    # ---------------------------------------------------------------------------
    #   Events dispatcher
    # ---------------------------------------------------------------------------
    @property
    def use_message_broker(self) -> bool:
        return self.config.app_config.COMMUNICATE_WITH_OURANOS

    async def init_message_broker(self) -> None:
        if not self.use_message_broker:
            raise RuntimeError(
                "Cannot initialize the message broker if the parameter "
                "'COMMUNICATE_WITH_OURANOS' is set to 'False'."
            )
        if self._message_broker is not None:
            raise RuntimeError("The message broker has already been initialized.")
        broker_url = self.config.app_config.AGGREGATOR_COMMUNICATION_URL
        if not broker_url:
            raise RuntimeError(
                "Cannot initialize the message broker if the parameter "
                "'AGGREGATOR_COMMUNICATION_URL' is not set."
            )
        from gaia.events import Events  # This will check the dependencies

        try:
            broker_type = broker_url[: broker_url.index("://")]
        except ValueError:
            raise ValueError(f"'{broker_url}' is not a valid broker URL")
        brokers_available = ["amqp", "redis"]
        if self.config.app_config.TESTING:
            brokers_available.append("memory")
        if broker_type not in brokers_available:
            raise ValueError(f"{broker_type} is not supported")
        self.logger.info("Initialising the event dispatcher.")
        if broker_type == "amqp":
            from dispatcher import AsyncAMQPDispatcher

            if broker_url == "amqp://":
                broker_url = "amqp://guest:guest@localhost:5672//"

            self.message_broker = AsyncAMQPDispatcher(
                "gaia",
                url=broker_url,
                queue_options={
                    "name": f"gaia-{self.config.app_config.ENGINE_UID}",
                    "durable": True,
                    "arguments": {
                        # Delete the queue after one week, CRUD requests will be lost
                        #  at this point
                        "x-expires": 60 * 60 * 24 * 7 * 1000,
                    },
                },
            )
        elif broker_type == "redis":
            from dispatcher import AsyncRedisDispatcher

            if broker_url == "redis://":
                broker_url = "redis://localhost:6379/0"
            self.message_broker = AsyncRedisDispatcher(
                "gaia",
                url=broker_url,
                queue_options={
                    "name": f"gaia-{self.config.app_config.ENGINE_UID}",
                },
            )
        elif broker_type == "memory":
            from dispatcher import AsyncInMemoryDispatcher

            self.message_broker = AsyncInMemoryDispatcher("gaia")
        events_handler = Events(engine=self)
        self.message_broker.register_event_handler(events_handler)
        self.event_handler = events_handler

    async def start_message_broker(self) -> None:
        self.logger.info("Starting the event dispatcher.")
        await self.message_broker.start(retry=True, block=False)

    async def stop_message_broker(self) -> None:
        self.logger.info("Stopping the event dispatcher.")
        await self.message_broker.stop()

    @property
    def message_broker_started(self) -> bool:
        if self._message_broker is None:
            return False
        return self._message_broker.running

    @property
    def message_broker(self) -> AsyncDispatcher:
        if self._message_broker is None:
            raise AttributeError(
                "'message_broker' is not valid as the message broker between "
                "Ouranos and Gaia is not used. To use it, set the Gaia app config "
                "parameter 'COMMUNICATE_WITH_OURANOS' to True, and the "
                "parameter 'AGGREGATOR_COMMUNICATION_URL' to a valid url"
            )
        return self._message_broker

    @message_broker.setter
    def message_broker(self, value: AsyncDispatcher | None) -> None:
        self._message_broker = value

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
    @property
    def use_db(self) -> bool:
        return self.config.app_config.USE_DATABASE

    async def init_database(self) -> None:
        if not self.use_db:
            raise RuntimeError(
                "Cannot initialize the database if the parameter 'USE_DATABASE' "
                "is set to 'False'."
            )
        if self._db is not None:
            raise RuntimeError("The database has already been initialized.")
        self.logger.info("Initialising the database.")
        from gaia.database import db

        self.db = db
        dict_cfg = {
            key: getattr(self.config.app_config, key)
            for key in dir(self.config.app_config)
            if key.isupper()
        }
        self.db.init(dict_cfg)
        await self.db.create_all()

    async def _reset_db_exchanges_uuid(self) -> None:
        # Reset buffered data's "exchange_uuid"
        from gaia.database.models import (
            ActuatorBuffer, DataBufferMixin, HealthBuffer, SensorBuffer)

        async with self.db.scoped_session() as session:
            for db_model in (ActuatorBuffer, HealthBuffer, SensorBuffer):
                db_model: DataBufferMixin
                await db_model.reset_ongoing_exchanges(session)

    async def start_database(self) -> None:
        self.logger.info("Starting the database.")
        await self._reset_db_exchanges_uuid()
        # Set up logging routines
        from gaia.database import routines

        if self.config.app_config.SENSORS_LOGGING_PERIOD is not None:
            cron_minute: str = self.config.app_config.SENSORS_LOGGING_PERIOD
            loop_period = self.config.app_config.SENSORS_LOOP_PERIOD
            seconds_offset = ceil(loop_period * 1.5)
            job_kwargs = {"scoped_session_": self.db.scoped_session, "engine": self}
            self.scheduler.add_job(
                func=routines.log_sensors_data,
                kwargs=job_kwargs,
                id="log_sensors_data",
                trigger=CronTrigger(
                    minute=cron_minute,
                    second=seconds_offset,
                    jitter=1.5,
                ),
                misfire_grace_time=10,
            )
        self._db_started = True

    async def stop_database(self) -> None:
        self.logger.info("Stopping the database.")
        if self.config.app_config.SENSORS_LOGGING_PERIOD:
            self.scheduler.remove_job("log_sensors_data")
        self._db_started = False

    @property
    def db_started(self) -> bool:
        return self._db_started

    @property
    def db(self) -> AsyncSQLAlchemyWrapper:
        if self._db is None:
            raise AttributeError(
                "'db' is not valid as the database is currently not used. To use "
                "it, set the Gaia app config parameter 'USE_DATABASE' to True"
            )
        return self._db

    @db.setter
    def db(self, value: AsyncSQLAlchemyWrapper | None) -> None:
        self._db = value

    # ---------------------------------------------------------------------------
    #   Plugins management
    # ---------------------------------------------------------------------------
    async def init_plugins(self) -> None:
        if not self.plugins_needed:
            raise RuntimeError(
                "Cannot initialize the plugins if neither the database, nor the "
                "event dispatcher is used."
            )
        self.logger.info("Initialising the plugins.")
        # Database
        if self.config.app_config.USE_DATABASE:
            await self.init_database()
        if (
            self.config.app_config.COMMUNICATE_WITH_OURANOS
            and self.config.app_config.AGGREGATOR_COMMUNICATION_URL
        ):
            await self.init_message_broker()
        self.plugins_initialized = True

    async def start_plugins(self) -> None:
        if not self.plugins_initialized:
            raise RuntimeError(
                "Cannot start plugins if they have not been initialised."
            )
        self.logger.info("Initialising the plugins.")
        if self.use_message_broker and not self.message_broker_started:
            await self.start_message_broker()
        if self.use_db and not self.db_started:
            await self.start_database()

    async def stop_plugins(self) -> None:
        self.logger.info("Stopping the plugins.")
        if self.use_message_broker and self.message_broker_started:
            await self.stop_message_broker()
        if self.use_db and self.db_started:
            await self.stop_database()

    # ---------------------------------------------------------------------------
    #   Engine functionalities
    # ---------------------------------------------------------------------------
    def start_background_tasks(self) -> None:
        self.logger.debug("Starting the background tasks.")
        self.scheduler.add_job(
            func=self.refresh_ecosystems_lighting_hours,
            id="refresh_sun_times",
            trigger=CronTrigger(hour="0", minute="0", second="5"),
            misfire_grace_time=15 * 60,
        )
        self.scheduler.add_job(
            func=self.update_chaos_time_window,
            id="refresh_chaos",
            trigger=CronTrigger(hour="0", minute="0", second="1"),
            misfire_grace_time=15 * 60,
        )
        self.scheduler.start()

    def stop_background_tasks(self) -> None:
        self.logger.debug("Stopping the background tasks.")
        self.scheduler.remove_job("refresh_sun_times")
        self.scheduler.remove_job("refresh_chaos")
        self.scheduler.remove_all_jobs()  # To be 100% sure
        self.scheduler.shutdown()

    async def _send_ecosystems_info(
            self,
            ecosystem_uids: str | list[str] | None = None,
    ) -> None:
        if self.message_broker_started and self.event_handler.registered:
            await self.event_handler.send_ecosystems_info(ecosystem_uids=ecosystem_uids)

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            async with self.config.new_config:
                await self.config.new_config.wait()
            if self.running:
                await self.refresh_ecosystems(send_info=True)
            if not self._stop_event.is_set():
                await sleep(0.1)  # Allow to do other stuff if too much config changes

    """
    API calls
    """
    @property
    def uid(self) -> str:
        return self._uid

    @property
    def started(self) -> bool:
        """Indicate if the Engine has been started."""
        return self._task is not None

    @property
    def running(self) -> bool:
        """Indicate if the Engine is running and managing the Ecosystems.

        An Engine can be started but not running, if the Ecosystem background
        tasks have been paused.

        The Engine background tasks are:
            - The loop managing the ecosystems.
            - The EngineConfig config files watchdog.
            - The plugins (the database and event broker if enabled)
        """
        return self._running_event.is_set() and not self._stop_event.is_set()

    @property
    def paused(self) -> bool:
        """Indicate if the Engine has paused its background tasks."""
        return self.started and not self._running_event.is_set()

    @property
    def stopping(self) -> bool:
        """Indicate if the Engine is stopping its background tasks."""
        return self._stop_event.is_set() and not self.stopped

    @property
    def stopped(self) -> bool:
        """Indicate if the Engine background tasks have been stopped and cleared."""
        return self._shut_down

    @property
    def ecosystems(self) -> dict[str, Ecosystem]:
        return self._ecosystems

    @property
    def config(self) -> EngineConfig:
        return self._config

    @property
    def places_list(self) -> list[gv.Place]:
        rv: list[gv.Place] = []
        places = self.config.places
        for place, coordinates in places.items():
            rv.append(
                gv.Place(
                    name=place,
                    coordinates=coordinates,
                )
            )
        return rv

    @property
    def ecosystems_started(self) -> set[str]:
        return set([
            ecosystem.uid
            for ecosystem in self.ecosystems.values()
            if ecosystem.started
        ])

    @property
    def task(self) -> Task:
        if self._task is None:
            raise AttributeError("Engine thread has not been set up")
        else:
            return self._task

    @task.setter
    def task(self, task: Task | None):
        self._task = task

    # ---------------------------------------------------------------------------
    #   Ecosystem managements
    # ---------------------------------------------------------------------------
    def get_ecosystem(self, ecosystem_uid: str) -> Ecosystem:
        """Get the required Ecosystem

        :param ecosystem_uid: The uid of an ecosystem, as written in 'ecosystems.cfg'
        """
        try:
            return self.ecosystems[ecosystem_uid]
        except KeyError:
            raise ValueError(f"Ecosystem '{ecosystem_uid}' is not linked to this engine")

    async def add_ecosystem(self, ecosystem_uid: str) -> Ecosystem:
        """Create an Ecosystem and link it to the Engine.

        :param ecosystem_uid: The uid of an ecosystem, as written in 'ecosystems.cfg'
        """
        ecosystem_uid, ecosystem_name = self.config.get_IDs(ecosystem_uid)
        if self.ecosystems.get(ecosystem_uid):
            raise RuntimeError(f"Ecosystem {ecosystem_uid} already exists")
        ecosystem = await Ecosystem.initialize(ecosystem_uid, self)
        self.ecosystems[ecosystem_uid] = ecosystem
        self.logger.debug(f"Ecosystem {ecosystem_uid} has been created.")
        return ecosystem

    async def start_ecosystem(self, ecosystem_uid: str) -> None:
        """Start an Ecosystem.

        :param ecosystem_uid: The uid of an ecosystem, as written in 'ecosystems.cfg'
        """
        ecosystem = self.get_ecosystem(ecosystem_uid)
        if ecosystem.started:
            raise RuntimeError(f"Ecosystem {ecosystem_uid} is already running")

        self.logger.debug(f"Starting ecosystem {ecosystem_uid}.")
        await ecosystem.start()

    async def stop_ecosystem(self, ecosystem_uid: str) -> None:
        """Stop an Ecosystem.

        :param ecosystem_uid: The uid of an ecosystem, as written in 'ecosystems.cfg'
        """
        ecosystem = self.get_ecosystem(ecosystem_uid)
        if not ecosystem.started:
            raise RuntimeError(f"Ecosystem {ecosystem_uid} is not running")

        await ecosystem.stop()
        self.logger.info(f"Ecosystem {ecosystem_uid} has been stopped")

    async def remove_ecosystem(self, ecosystem_uid: str) -> None:
        """Terminate an Ecosystem and remove it from Engine's memory.

        :param ecosystem_uid: The uid of an ecosystem, as written in 'ecosystems.cfg'
        """
        ecosystem = self.get_ecosystem(ecosystem_uid)
        if ecosystem.uid in self.ecosystems_started:
            raise RuntimeError("Cannot dismount a started ecosystem. First stop it")

        await ecosystem.terminate()
        del self.ecosystems[ecosystem.uid], ecosystem
        self.logger.info(f"Ecosystem {ecosystem_uid} has been dismounted")

    def _humanize_eco_set(self, ecosystem_uid_set: set[str]) -> str:
        return humanize_list(
            [
                f"'{self.config.get_ecosystem_name(ecosystem_uid)}'"
                for ecosystem_uid in ecosystem_uid_set
            ],
        )

    async def refresh_ecosystems(self, send_info: bool = True):
        """Starts and stops the Ecosystem based on the 'ecosystem.cfg' file.

        :param send_info: If `True`, will try to send the ecosystem info to
                          Ouranos if possible.
        """
        self.logger.info("Refreshing the ecosystems ...")
        expected_to_run = set(self.config.get_ecosystems_expected_to_run())

        # First stop the ecosystems not expected to run
        self.logger.debug("Looking for ecosystems not expected to be running.")
        to_stop = self.ecosystems_started - expected_to_run
        for ecosystem_uid in to_stop:
            await self.stop_ecosystem(ecosystem_uid)

        # Then, delete the ecosystems which are no longer in the config file
        self.logger.debug(
            "Looking for ecosystems that are no longer in the config file.")
        to_delete = set(self.ecosystems.keys()) - set(self.config.ecosystems_uid)
        for ecosystem_uid in to_delete:
            await self.remove_ecosystem(ecosystem_uid)

        # Initialize the ecosystems found in the config file but not yet initialized
        self.logger.debug(
            "Looking for ecosystems present in the config file but not yet initialized.")
        to_initialize = set(self.config.ecosystems_uid) - set(self.ecosystems.keys())
        for ecosystem_uid in to_initialize:
            await self.add_ecosystem(ecosystem_uid)

        # Start the ecosystems which are expected to run and are not running
        self.logger.debug(
            "Looking for ecosystems expected to be running but not yet started.")
        already_started = self.ecosystems_started
        to_start = expected_to_run - self.ecosystems_started
        for ecosystem_uid in to_start:
            await self.start_ecosystem(ecosystem_uid)

        # Refresh the ecosystems that were already running and did not stop
        self.logger.debug(
            "Looking for already running ecosystems that need to continue to run.")
        for ecosystem_uid in already_started:
            await self.ecosystems[ecosystem_uid].refresh_hardware()
            await self.ecosystems[ecosystem_uid].refresh_subroutines()
            await self.ecosystems[ecosystem_uid].refresh_lighting_hours(send_info=False)
        # self.refresh_ecosystems_lighting_hours()  # done by Ecosystem during their startup

        if send_info:
            await self._send_ecosystems_info()

    async def terminate_ecosystems(self) -> None:
        for ecosystem_uid in [*self.ecosystems.keys()]:
            await self.remove_ecosystem(ecosystem_uid)

    async def refresh_ecosystems_lighting_hours(self, send_info: bool = True) -> None:
        """Refresh all the Ecosystems lighting hours

        Should only be called routinely, once a day. Other than that, Ecosystems
        will try to compute their lighting hours based on the method chosen and
        get recent sun times if needed by the method."""
        self.logger.info("Refreshing ecosystems lighting hours.")
        self.config.refresh_sun_times()
        for ecosystem in self.ecosystems.values():
            if ecosystem.started:
                await ecosystem.refresh_lighting_hours(send_info=False)
        if send_info and self.message_broker_started:
            await self.event_handler.send_payload_if_connected("nycthemeral_info")

    async def update_chaos_time_window(self, send_info: bool = True) -> None:
        self.logger.info("Updating ecosystems chaos time window.")
        for ecosystem in self.ecosystems.values():
            await ecosystem.config.update_chaos_time_window(send_info=False)
        await self.config.save(CacheType.chaos)
        if send_info and self.message_broker_started:
            await self.event_handler.send_payload_if_connected("chaos_parameters")

    # ---------------------------------------------------------------------------
    #   Engine start and stop
    # ---------------------------------------------------------------------------
    async def start(self) -> None:
        """Start the Engine

        When started, the Engine will automatically manage the Ecosystems based
        on the 'ecosystem.cfg' file and refresh the Ecosystems when changes are
        made in the file.
        """
        if self.started:
            raise RuntimeError("Engine can only be started once.")
        if self.running:  # pragma: no cover
            raise RuntimeError("Engine can only be started once.")
        if self.stopped:
            raise RuntimeError("Cannot restart a shut down engine.")
        self.logger.info("Starting Gaia ...")
        if self.plugins_needed and not self.plugins_initialized:
            raise RuntimeError(
                "Some plugins are needed but have not been initialized. Please "
                "use the 'init_plugins()' method to start them."
            )
        # Load the ecosystem configs into memory and start the watchdog
        await self.config.initialize_configs()
        self.config.start_watchdog()
        # Start background tasks and plugins
        self.start_background_tasks()
        if self.plugins_initialized:
            await self.start_plugins()
        # Start the engine thread
        self.task = asyncio.create_task(self._loop(), name="engine-loop")
        # Refresh ecosystems a first time
        await sleep(0)  # Allow _loop() to start
        await self._resume()
        self.logger.info("Gaia started.")

    async def wait(self):
        if self.running:
            self.logger.info("Running ...")
            while self.running:
                await sleep(0.5)
        else:
            raise RuntimeError("Gaia needs to be started in order to wait.")

    def pause(self) -> None:
        if not self.running:
            raise RuntimeError("Cannot pause a non-started engine")
        self.logger.info("Pausing Gaia ...")
        self.scheduler.pause()
        # Set the events so the loop continues but doesn't update anything
        self._running_event.clear()

    async def _resume(self) -> None:
        if self.stopped:
            raise RuntimeError("Cannot resume a stopped engine.")
        # Set the events
        self._running_event.set()
        # Send a config signal so the loop unlocks and refreshed the ecosystems
        async with self.config.new_config:
            self.config.new_config.notify_all()
        self.scheduler.resume()

    async def resume(self) -> None:
        if self.running:
            raise RuntimeError("Cannot resume a running engine")
        self.logger.info("Resuming Gaia ...")
        await self._resume()

    def _handle_stop_signal(self) -> None:
        self.logger.info("Received a 'stop' signal")
        self.stop()

    def stop(self) -> None:
        """Shutdown the Engine"""
        if not self.started:
            raise RuntimeError("Cannot stop a non-started engine.")
        if self.stopped:
            raise RuntimeError("Cannot stop an already stopped engine.")
        self._stop_event.set()

    async def shutdown(self) -> None:
        if self.running:
            self.pause()
        self.logger.info("Shutting down Gaia ...")
        # Stop the loop
        # Set the cleaning up event
        self._stop_event.set()
        # Send a config signal so the loops unlocks ... and stops
        async with self.config.new_config:
            self.config.new_config.notify_all()
        self.task.cancel()
        self.task = None
        # Stop and dismount ecosystems
        for ecosystem_uid in [*self.ecosystems_started]:
            await self.stop_ecosystem(ecosystem_uid)
        await self.terminate()
        self.config.stop_watchdog()
        self.stop_background_tasks()
        self._shut_down = True
        self.logger.info("Gaia has shut down")

    def add_signal_handler(self) -> None:
        assert threading.current_thread() is threading.main_thread()

        def signal_handler(signum, frame) -> None:
            self._handle_stop_signal()

        loop = asyncio.get_event_loop()

        try:
            for sig in SIGNALS:
                loop.add_signal_handler(sig, signal_handler, sig, None)
        except NotImplementedError:
            for sig in SIGNALS:
                signal.signal(sig, signal_handler)

    async def run(self) -> None:
        self.add_signal_handler()
        await self.start()
        await self.wait()
        await self.shutdown()
