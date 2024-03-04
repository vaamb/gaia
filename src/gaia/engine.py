from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import logging.config
from math import ceil
import signal
from threading import Event, Thread
from time import sleep
import typing as t

from apscheduler.executors.pool import BasePoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import gaia_validators as gv

from gaia.config.from_files import CacheType, EngineConfig
from gaia.ecosystem import Ecosystem
from gaia.utils import SingletonMeta
from gaia.virtual import VirtualWorld


if t.TYPE_CHECKING:
    from dispatcher import KombuDispatcher
    from sqlalchemy_wrapper import SQLAlchemyWrapper

    from gaia.events import Events


SIGNALS = (
    signal.SIGINT,
    signal.SIGTERM,
)


class APSchedulerExecutor(BasePoolExecutor):
    # Adapt the recipe from apscheduler.executors.pool.ThreadPoolExecutor to use
    #  an existing concurrent.futures.ThreadPoolExecutor
    def __init__(self, pool: ThreadPoolExecutor):
        super().__init__(pool)


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
        self.logger: logging.Logger = logging.getLogger(f"gaia.engine")
        self.logger.info("Initializing Gaia.")
        self._ecosystems: dict[str, Ecosystem] = {}
        self._uid: str = self.config.app_config.ENGINE_UID
        self._virtual_world: VirtualWorld | None = None
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
                thread_name_prefix=f"Engine_ThreadPoolExecutor", max_workers=10)
        self._scheduler: BackgroundScheduler = BackgroundScheduler(
            executors={"default": APSchedulerExecutor(self._executor)})
        if self.config.app_config.VIRTUALIZATION:
            self.logger.info("Using ecosystem virtualization.")
            virtual_cfg = self.config.app_config.VIRTUALIZATION_PARAMETERS
            virtual_world_cfg: dict = virtual_cfg.get("world", {})
            self._virtual_world = VirtualWorld(self, **virtual_world_cfg)
        self._message_broker: KombuDispatcher | None = None
        self._event_handler: Events | None = None
        self._db: SQLAlchemyWrapper | None = None
        self.plugins_initialized: bool = False
        self._thread: Thread | None = None
        self._running_event = Event()
        self._stop_event = Event()
        self._shut_down: bool = False

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._uid}, config={self.config})"

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
    def executor(self) -> ThreadPoolExecutor:
        return self._executor

    @property
    def scheduler(self) -> BackgroundScheduler:
        return self._scheduler

    # ---------------------------------------------------------------------------
    #   Events dispatcher
    # ---------------------------------------------------------------------------
    def init_message_broker(self) -> None:
        if not self.config.app_config.COMMUNICATE_WITH_OURANOS:
            raise RuntimeError(
                "Cannot initialize the message broker if the parameter "
                "'COMMUNICATE_WITH_OURANOS' is set to 'False'."
            )
        if self.use_message_broker:
            raise RuntimeError(
                "The message broker has already been initialized."
            )
        broker_url = self.config.app_config.AGGREGATOR_COMMUNICATION_URL
        if not broker_url:
            raise RuntimeError(
                "Cannot initialize the message broker if the parameter "
                "'AGGREGATOR_COMMUNICATION_URL' is not set."
            )
        try:
            broker_type = broker_url[:broker_url.index("://")]
        except ValueError:
            raise ValueError(f"'{broker_url}' is not a valid broker URL")
        brokers_available = ["amqp", "redis"]
        if self.config.app_config.TESTING:
            brokers_available.append("memory")
        if broker_type not in brokers_available:
            raise ValueError(f"{broker_type} is not supported")
        self.logger.info("Initialising the event dispatcher")
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
                "name": f"gaia-{self.config.app_config.ENGINE_UID}",
                # Delete the queue after one week, CRUD requests will be lost
                #  at this point
                "expires": 60 * 60 * 24 * 7
            })
        from gaia.events import Events
        events_handler = Events(engine=self)
        self.message_broker.register_event_handler(events_handler)
        self.event_handler = events_handler

    def start_message_broker(self) -> None:
        self.logger.info("Starting the event dispatcher.")
        self.message_broker.start(retry=True, block=False)

    def stop_message_broker(self) -> None:
        self.logger.info("Stopping the event dispatcher.")
        self.message_broker.stop()

    @property
    def message_broker(self) -> "KombuDispatcher":
        if self._message_broker is None:
            raise AttributeError(
                "'message_broker' is not valid as the message broker between "
                "Ouranos and Gaia is not used. To use it, set the Gaia app config "
                "parameter 'COMMUNICATE_WITH_OURANOS' to True, and the "
                "parameter 'AGGREGATOR_COMMUNICATION_URL' to a valid url")
        return self._message_broker

    @message_broker.setter
    def message_broker(self, value: "KombuDispatcher" | None) -> None:
        self._message_broker = value

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
    def init_database(self) -> None:
        if not self.config.app_config.USE_DATABASE:
            raise RuntimeError(
                "Cannot initialize the database if the parameter 'USE_DATABASE' "
                "is set to 'False'."
            )
        if self.use_db:
            raise RuntimeError(
                "The database has already been initialized."
            )
        self.logger.info("Initialising the database.")
        from gaia.database import db
        self.db = db
        dict_cfg = {
            key: getattr(self.config.app_config, key)
            for key in dir(self.config.app_config)
            if key.isupper()
        }
        self.db.init(dict_cfg)
        self.db.create_all()

    def start_database(self) -> None:
        self.logger.info("Starting the database.")
        from gaia.database import routines
        if self.config.app_config.SENSORS_LOGGING_PERIOD is not None:
            cron_minute: str = self.config.app_config.SENSORS_LOGGING_PERIOD
            loop_period = self.config.app_config.SENSORS_LOOP_PERIOD
            seconds_offset = ceil(loop_period * 1.5)
            job_kwargs = {"scoped_session_": self.db.scoped_session, "engine": self}
            self.scheduler.add_job(
                func=routines.log_sensors_data, kwargs=job_kwargs,
                id="log_sensors_data",
                trigger=CronTrigger(minute=cron_minute, second=seconds_offset, jitter=1.5),
                misfire_grace_time=10,
            )

    def stop_database(self) -> None:
        self.logger.info("Stopping the database.")
        if self.config.app_config.SENSORS_LOGGING_PERIOD:
            self.scheduler.remove_job("log_sensors_data")

    @property
    def db(self) -> "SQLAlchemyWrapper":
        if self._db is None:
            raise AttributeError(
                "'db' is not valid as the database is currently not used. To use "
                "it, set the Gaia app config parameter 'USE_DATABASE' to True")
        return self._db

    @db.setter
    def db(self, value: "SQLAlchemyWrapper" | None) -> None:
        self._db = value

    @property
    def use_db(self) -> bool:
        return self._db is not None

    # ---------------------------------------------------------------------------
    #   Plugins management
    # ---------------------------------------------------------------------------
    def init_plugins(self) -> None:
        if not self.plugins_needed:
            raise RuntimeError(
                "Cannot initialize the plugins if neither the database, nor the "
                "event dispatcher is used."
            )
        self.logger.info("Initialising the plugins.")
        # Database
        if self.config.app_config.USE_DATABASE:
            self.init_database()
        if (
            self.config.app_config.COMMUNICATE_WITH_OURANOS
            and self.config.app_config.AGGREGATOR_COMMUNICATION_URL
        ):
            self.init_message_broker()
        self.plugins_initialized = True

    def start_plugins(self) -> None:
        if not self.plugins_initialized:
            raise RuntimeError(
                "Cannot start plugins if they have not been initialised."
            )
        self.logger.info("Initialising the plugins.")
        if self.use_message_broker:
            self.start_message_broker()
        if self.use_db:
            self.start_database()

    def stop_plugins(self) -> None:
        self.logger.info("Stopping the plugins.")
        if self.use_message_broker:
            self.stop_message_broker()
        if self.use_db:
            self.stop_database()

    # ---------------------------------------------------------------------------
    #   Engine functionalities
    # ---------------------------------------------------------------------------
    def start_background_tasks(self) -> None:
        self.logger.debug("Starting the background tasks.")
        self.scheduler.add_job(
            func=self.refresh_ecosystems_lighting_hours,
            id="refresh_sun_times",
            trigger=CronTrigger(hour="0", minute="1"),
            misfire_grace_time=15 * 60,
        )
        self.scheduler.add_job(
            func=self.update_chaos_time_window,
            id="refresh_chaos",
            trigger=CronTrigger(hour="0", minute="5"),
            misfire_grace_time=15 * 60,
        )
        self.scheduler.start()

    def stop_background_tasks(self) -> None:
        self.logger.debug("Stopping the background tasks.")
        self.scheduler.remove_job("refresh_sun_times")
        self.scheduler.remove_job("refresh_chaos")
        self.scheduler.remove_all_jobs()  # To be 100% sure
        self.scheduler.shutdown()

    def _send_ecosystem_info(self) -> None:
        if self.use_message_broker and self.event_handler.registered:
            self.event_handler.send_ecosystems_info()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            with self.config.new_config:
                self.config.new_config.wait()
            if self.running:
                self.refresh_ecosystems(send_info=True)
            if not self._stop_event.is_set():
                sleep(0.1)  # Allow to do other stuff if too much config changes

    """
    API calls
    """
    @property
    def uid(self) -> str:
        return self._uid

    @property
    def started(self) -> bool:
        """Indicate if the Engine has been started."""
        return self._thread is not None

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
        return (
            self._running_event.is_set()
            and not self._stop_event.is_set()
        )

    @property
    def paused(self) -> bool:
        """Indicate if the Engine has paused its background tasks."""
        return (
            self.started
            and not self._running_event.is_set()
        )

    @property
    def stopping(self) -> bool:
        """Indicate if the Engine is stopping its background tasks."""
        return self._stop_event.is_set() and not self.stopped

    @property
    def stopped(self) -> bool:
        """Indicate if the Engine background tasks have been stopped and cleared.
        """
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
            rv.append(gv.Place(
                name=place,
                coordinates=coordinates,
            ))
        return rv

    @property
    def ecosystems_started(self) -> set[str]:
        return set([
            ecosystem.uid for ecosystem in self.ecosystems.values()
            if ecosystem.started
        ])

    @property
    def thread(self) -> Thread:
        if self._thread is None:
            raise AttributeError("Engine thread has not been set up")
        else:
            return self._thread

    @thread.setter
    def thread(self, thread: Thread | None):
        self._thread = thread

    # ---------------------------------------------------------------------------
    #   Ecosystem managements
    # ---------------------------------------------------------------------------
    def init_ecosystem(self, ecosystem_id: str, start: bool = False) -> Ecosystem:
        """Initialize an Ecosystem.

        :param ecosystem_id: The name or the uid of an ecosystem, as written in
                             'ecosystems.cfg'.
        :param start: Whether to immediately start the ecosystem after its
                      creation or not.
        """
        ecosystem_uid, ecosystem_name = self.config.get_IDs(ecosystem_id)
        if ecosystem_uid not in self.ecosystems:
            ecosystem = Ecosystem(ecosystem_uid, self)
            self.ecosystems[ecosystem_uid] = ecosystem
            self.logger.debug(
                f"Ecosystem {ecosystem_id} has been created"
            )
            if start:
                self.start_ecosystem(ecosystem_uid)
            return ecosystem
        raise RuntimeError(
            f"Ecosystem {ecosystem_id} already exists"
        )

    def start_ecosystem(self, ecosystem_id: str, send_info: bool = False) -> None:
        """Start an Ecosystem.

        :param ecosystem_id: The name or the uid of an ecosystem, as written in
                             'ecosystems.cfg'.
        :param send_info: If `True`, will try to send the ecosystem info to
                          Ouranos if possible.
        """
        ecosystem_uid, ecosystem_name = self.config.get_IDs(ecosystem_id)
        if ecosystem_uid in self.ecosystems:
            if ecosystem_uid not in self.ecosystems_started:
                ecosystem: Ecosystem = self.ecosystems[ecosystem_uid]
                self.logger.debug(
                    f"Starting ecosystem {ecosystem_id}"
                )
                ecosystem.start()
                if send_info:
                    self._send_ecosystem_info()
            else:
                raise RuntimeError(
                    f"Ecosystem {ecosystem_id} is already running"
                )
        else:
            raise RuntimeError(
                f"Need to initialise Ecosystem {ecosystem_id} first"
            )

    def stop_ecosystem(
            self,
            ecosystem_id: str,
            dismount: bool = False,
            send_info: bool = False,
    ) -> None:
        """Stop an Ecosystem.

        :param ecosystem_id: The name or the uid of an ecosystem, as written in
                             'ecosystems.cfg'.
        :param dismount: Whether to remove the Ecosystem from the memory or not.
                         If dismounted, the Ecosystem will need to be recreated
                         before being able to restart.
        :param send_info: If `True`, will try to send the ecosystem info to
                  Ouranos if possible.
        """
        if ecosystem_id in self.ecosystems:
            ecosystem_uid = ecosystem_id
        else:
            ecosystem_uid, _ = self.config.get_IDs(ecosystem_id)
        if ecosystem_uid in self.ecosystems:
            if ecosystem_uid in self.ecosystems_started:
                ecosystem = self.ecosystems[ecosystem_uid]
                ecosystem.stop()
                if dismount:
                    self.dismount_ecosystem(ecosystem_uid)
                if send_info:
                    self._send_ecosystem_info()
                self.logger.info(
                    f"Ecosystem {ecosystem_id} has been stopped"
                )
            else:
                raise RuntimeError(
                    f"Cannot stop Ecosystem {ecosystem_id} as it has not been "
                    f"started"
                )
        else:
            raise RuntimeError(
                f"Cannot stop Ecosystem {ecosystem_id} as it has not been "
                f"initialised"
            )

    def dismount_ecosystem(self, ecosystem_id: str, send_info: bool = False) -> None:
        """Remove the Ecosystem from Engine's memory.

        :param ecosystem_id: The name or the uid of an ecosystem, as written in
                             'ecosystems.cfg'.
        :param send_info: If `True`, will try to send the ecosystem info to
                  Ouranos if possible.
        """
        if ecosystem_id in self.ecosystems:
            ecosystem_uid = ecosystem_id
        else:
            ecosystem_uid, _ = self.config.get_IDs(ecosystem_id)
        if ecosystem_uid in self.ecosystems:
            if ecosystem_uid in self.ecosystems_started:
                raise RuntimeError(
                    "Cannot dismount a started ecosystem. First stop it"
                )
            else:
                del self.ecosystems[ecosystem_uid]
                if send_info:
                    self._send_ecosystem_info()
                self.logger.info(
                    f"Ecosystem {ecosystem_id} has been dismounted"
                )
        else:
            raise RuntimeError(
                f"Cannot dismount ecosystem {ecosystem_id} as it has not been "
                f"initialised"
            )

    def get_ecosystem(self, ecosystem_id: str) -> Ecosystem:
        """Get the required Ecosystem

        :param ecosystem_id: The name or the uid of an ecosystem, as written in
                            'ecosystems.cfg'
        """
        ecosystem_uid, ecosystem_name = self.config.get_IDs(ecosystem_id)
        if ecosystem_uid in self.ecosystems:
            ecosystem = self.ecosystems[ecosystem_uid]
        else:
            ecosystem = self.init_ecosystem(ecosystem_uid)
        return ecosystem

    def refresh_ecosystems(self, send_info: bool = False):
        """Starts and stops the Ecosystem based on the 'ecosystem.cfg' file.

        :param send_info: If `True`, will try to send the ecosystem info to
          Ouranos if possible.
        """
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
            self.start_ecosystem(ecosystem_uid)
        # stop Ecosystems which are not expected to run and are currently
        # running
        to_stop = self.ecosystems_started - expected_started
        for ecosystem_uid in to_stop:
            self.stop_ecosystem(ecosystem_uid)
        # refresh Ecosystems that were already running and did not stop
        started_before = self.ecosystems_started - to_start
        for ecosystem_uid in started_before:
            self.ecosystems[ecosystem_uid].refresh_subroutines()
            self.ecosystems[ecosystem_uid].refresh_lighting_hours(send=False)
        # delete Ecosystems which were created and are no longer on the
        # config file
        for ecosystem_uid in to_delete:
            if self.ecosystems[ecosystem_uid].started:
                self.stop_ecosystem(ecosystem_uid)
            self.dismount_ecosystem(ecosystem_uid)
        # self.refresh_ecosystems_lighting_hours()  # done by Ecosystem during their startup
        if send_info:
            self._send_ecosystem_info()

    def refresh_ecosystems_lighting_hours(self, send: bool = True) -> None:
        """Refresh all the Ecosystems lighting hours

        Should only be called routinely, once a day. Other than that, Ecosystems
        will try to compute their lighting hours based on the method chosen and
        get recent sun times if needed by the method."""
        self.logger.info("Refreshing ecosystems lighting hours.")
        self.config.refresh_sun_times()
        for ecosystem in self.ecosystems.values():
            if ecosystem.started:
                ecosystem.refresh_lighting_hours(send=False)
        if send and self.use_message_broker:
            self.event_handler.send_payload_if_connected("light_data")

    def update_chaos_time_window(self, send: bool = True) -> None:
        self.logger.info("Updating ecosystems chaos time window.")
        for ecosystem in self.ecosystems.values():
            ecosystem.config.update_chaos_time_window(send=False)
        self.config.save(CacheType.chaos)
        if send and self.use_message_broker:
            self.event_handler.send_payload_if_connected("chaos_parameters")

    # ---------------------------------------------------------------------------
    #   Engine start and stop
    # ---------------------------------------------------------------------------
    def start(self) -> None:
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
        self.config.initialize_configs()
        self.config.start_watchdog()
        # Start background tasks and plugins
        self.start_background_tasks()
        if self.plugins_initialized:
            self.start_plugins()
        # Start the engine thread
        self.thread = Thread(
            target=self._loop,
            name="Engine_LoopThread",
            daemon=True,
        )
        self.thread.start()
        # Refresh ecosystems a first time
        self._resume()
        self.logger.info("Gaia started")

    def wait(self):
        if self.running:
            self.logger.info("Running")
            while self.running:
                sleep(0.5)
        else:
            raise RuntimeError("Gaia needs to be started in order to wait")

    def pause(self) -> None:
        if not self.running:
            raise RuntimeError("Cannot pause a non-started engine")
        self.logger.info("Pausing Gaia ...")
        self.scheduler.pause()
        # Set the events so the loop continues but doesn't update anything
        self._running_event.clear()

    def _resume(self) -> None:
        if self.stopped:
            raise RuntimeError("Cannot resume a stopped engine.")
        # Set the events
        self._running_event.set()
        # Send a config signal so the loop unlocks and refreshed the ecosystems
        with self.config.new_config:
            self.config.new_config.notify_all()
        self.scheduler.resume()

    def resume(self) -> None:
        if self.running:
            raise RuntimeError("Cannot resume a running engine")
        self.logger.info("Resuming Gaia ...")
        self._resume()

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

    def shutdown(self) -> None:
        if self.running:
            self.pause()
        self.logger.info("Shutting down Gaia ...")
        # Stop the loop
        # Set the cleaning up event
        self._stop_event.set()
        # Send a config signal so the loops unlocks ... and stops
        with self.config.new_config:
            self.config.new_config.notify_all()
        self.thread.join()
        self.thread = None
        # Stop and dismount ecosystems
        for ecosystem_uid in [*self.ecosystems_started]:
            self.stop_ecosystem(ecosystem_uid)
        to_delete = [*self.ecosystems.keys()]
        for ecosystem in to_delete:
            self.dismount_ecosystem(ecosystem)
        # Stop plugins and background tasks
        if self.plugins_initialized:
            self.stop_plugins()
        self.config.stop_watchdog()
        self.stop_background_tasks()
        self.executor.shutdown()
        self._shut_down = True
        self.logger.info("Gaia has shut down")

    def add_signal_handler(self) -> None:
        def signal_handler(signum, frame) -> None:
            self._handle_stop_signal()

        for sig in SIGNALS:
            signal.signal(sig, signal_handler)

    def run(self) -> None:
        self.add_signal_handler()
        self.start()
        self.wait()
        self.shutdown()
