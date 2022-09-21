from json.decoder import JSONDecodeError
import logging
import logging.config
import os
from threading import Thread
import typing as t
import weakref

from .config_parser import config_event, detach_config, GeneralConfig, get_IDs
from .ecosystem import Ecosystem
from .events import Events
from .exceptions import UndefinedParameter
from .shared_resources import scheduler, start_scheduler
from .utils import json, SingletonMeta
from .virtual import get_virtual_ecosystem
from config import Config


class Engine(metaclass=SingletonMeta):
    """An Engine class that will coordinate several Ecosystem instances.

    Under normal circumstances only one Ecosystem instance should be created
    for each ecosystem. The Engine makes sure this is the case. It also
    manages the config watchdog and updates the sun times once a day.
    When used within Gaia, the Engine is automatically instantiated when needed.

    :param general_config: a GeneralConfig object
    """
    def __init__(self, general_config: GeneralConfig) -> None:
        self._config: GeneralConfig = weakref.proxy(general_config)
        self.logger: logging.Logger = logging.getLogger(
            f"{Config.APP_NAME.lower()}.engine"
        )
        self.logger.debug("Initializing")
        self._ecosystems: dict[str, Ecosystem] = {}
        self._uid: str = Config.UUID
        self._run: bool = False
        self._event_handler: t.Union[Events, None] = None

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._uid}, config={self.config})"

    def _start_background_tasks(self) -> None:
        self.logger.debug("Starting background tasks")
        self.config.start_watchdog()
        cache_dir = self.config.base_dir/"cache"
        if not cache_dir.exists():
            os.mkdir(cache_dir)
        self.refresh_sun_times()
        scheduler.add_job(self.refresh_sun_times, "cron",
                          hour="1", misfire_grace_time=15 * 60,
                          id="refresh_sun_times")
        scheduler.add_job(self.refresh_chaos, "cron",
                          hour="0", minute="5", misfire_grace_time=15 * 60,
                          id="refresh_chaos")
        start_scheduler()

    def _stop_background_tasks(self) -> None:
        self.logger.debug("Stopping background tasks")
        self.config.stop_watchdog()
        scheduler.remove_job("refresh_sun_times")
        scheduler.remove_job("refresh_chaos")

    def _engine_startup(self) -> None:
        if Config.VIRTUALIZATION:
            for ecosystem_uid in self.config.ecosystems_uid:
                get_virtual_ecosystem(ecosystem_uid, start=True)
        self.refresh_ecosystems()

    def _loop(self) -> None:
        while self._run:
            with config_event:
                config_event.wait()
            if not self._run:
                break
            self.refresh_ecosystems()
            if self.event_handler:
                self.event_handler.send_config()
                self.event_handler.send_light_data()

    """
    API calls
    """
    @property
    def ecosystems(self):
        return self._ecosystems

    @property
    def config(self):
        return self._config

    @property
    def ecosystems_started(self) -> set:
        return set([
            ecosystem.uid for ecosystem in self.ecosystems.values()
            if ecosystem.status
        ])

    @property
    def event_handler(self):
        """Return the event handler

        Either a Socketio Namespace or dispatcher EventHandler
        """
        return self._event_handler

    @event_handler.setter
    def event_handler(self, event_handler: Events):
        self._event_handler = event_handler

    def init_ecosystem(self, ecosystem_id: str, start: bool = False) -> Ecosystem:
        """Initialize an Ecosystem

        :param ecosystem_id: The name or the uid of an ecosystem, as written in
                             'ecosystems.cfg'
        :param start: Whether to immediately start the ecosystem after its
                      creation or not
        """
        ecosystem_uid, ecosystem_name = get_IDs(ecosystem_id)
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

    def start_ecosystem(self, ecosystem_id: str) -> None:
        """Start an Ecosystem

        :param ecosystem_id: The name or the uid of an ecosystem, as written in
                             'ecosystems.cfg'
        """
        ecosystem_uid, ecosystem_name = get_IDs(ecosystem_id)
        if ecosystem_uid in self.ecosystems:
            if ecosystem_uid not in self.ecosystems_started:
                ecosystem: Ecosystem = self.ecosystems[ecosystem_uid]
                self.logger.debug(
                    f"Starting ecosystem {ecosystem_name}"
                )
                ecosystem.start()
            else:
                raise RuntimeError(
                    f"Ecosystem {ecosystem_id} is already running"
                )
        else:
            raise RuntimeError(
                f"Neet to initialise Ecosystem {ecosystem_id} first"
            )

    def stop_ecosystem(self, ecosystem_id: str, dismount: bool = False) -> None:
        """Stop an Ecosystem

        :param ecosystem_id: The name or the uid of an ecosystem, as written in
                             'ecosystems.cfg'
        :param dismount: Whether to remove the Ecosystem from the memory or not.
                         If dismounted, the Ecosystem will need to be recreated
                         before being able to restart.
        """
        ecosystem_uid, ecosystem_name = get_IDs(ecosystem_id)
        if ecosystem_uid in self.ecosystems:
            if ecosystem_uid in self.ecosystems_started:
                ecosystem = self.ecosystems[ecosystem_uid]
                ecosystem.stop()
                if dismount:
                    self.dismount_ecosystem(ecosystem_uid)
                self.logger.info(
                    f"Ecosystem {ecosystem_name} has been stopped")
                # If no more ecosystem running, stop background routines
                if not self.ecosystems_started:
                    self._stop_background_tasks()
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
        ecosystem_id, ecosystem_name = get_IDs(ecosystem_id)
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
        ecosystem_uid, ecosystem_name = get_IDs(ecosystem)
        if ecosystem_uid in self.ecosystems:
            _ecosystem = self.ecosystems[ecosystem_uid]
        else:
            _ecosystem = self.init_ecosystem(ecosystem_uid)
        return _ecosystem

    def refresh_ecosystems(self):
        """Starts and stops the Ecosystem based on the 'ecosystem.cfg' file"""
        expected_started = set(self.config.get_ecosystems_expected_running())
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
        # delete Ecosystems which were created and are no longer on the
        # config file
        for ecosystem_uid in to_delete:
            self.stop_ecosystem(ecosystem_uid)
            self.dismount_ecosystem(ecosystem_uid)

    def refresh_sun_times(self) -> None:
        """Download sunrise and sunset times if needed by an Ecosystem"""
        self.logger.debug("Check if sun times need to be refreshed")
        need = []
        for ecosystem in self.ecosystems:
            try:
                if (
                    self.ecosystems[ecosystem].config.light_method in
                    ("mimic", "elongate")
                    # And expected to be running
                    and self.ecosystems[ecosystem].config.status
                ):
                    need.append(ecosystem)
            except UndefinedParameter:
                # Bad configuration file
                pass
        if any(need):
            try:
                self.config.download_sun_times()
            except ConnectionError:
                self.logger.error("The Engine could not download sun times")
                for ecosystem in need:
                    self.ecosystems[ecosystem].config.light_method = "fixed"
            else:
                for ecosystem in need:
                    try:
                        if self.ecosystems[ecosystem].status:
                            self.ecosystems[ecosystem].refresh_sun_times()
                    except KeyError:
                        # Occur
                        pass
        else:
            self.logger.debug("No need to refresh sun times")

    def refresh_chaos(self):
        for ecosystem in self.ecosystems.values():
            ecosystem.refresh_chaos()
        chaos_file = self.config.base_dir/"cache/chaos.json"
        try:
            with chaos_file.open("r+") as file:
                ecosystem_chaos = json.loads(file.read())
                ecosystems = list(ecosystem_chaos.keys())
                for ecosystem in ecosystems:
                    if ecosystem not in self.ecosystems:
                        del ecosystem_chaos[ecosystem]
                file.write(json.dumps(ecosystem_chaos))
        except (FileNotFoundError, JSONDecodeError):  # Empty or absent file
            pass

    def start(self) -> None:
        """Start the Engine

        When started, the Engine will automatically manage the Ecosystems based
        on the 'ecosystem.cfg' file and refresh the Ecosystems when changes are
        made in the file.
        """
        if not self._run:
            self.logger.info("Starting the Engine ...")
            self._start_background_tasks()
            self._engine_startup()
            self._thread = Thread(target=self._loop)
            self._thread.name = "engine"
            self._thread.start()
            self._run = True
            self.logger.info("Engine started")
        else:  # pragma: no cover
            raise RuntimeError("Engine can only be started once")

    def stop(
            self,
            stop_ecosystems: bool = True,
            clear_engine: bool = True
    ) -> None:
        """Stop the Engine"""
        if self._run:
            self.logger.info("Stopping the Engine ...")
            if clear_engine:
                stop_ecosystems = True
            self._run = False
            # send a config signal so a last loops starts
            with config_event:
                config_event.notify_all()
            self._thread.join()
            self._thread = None

            if stop_ecosystems:
                for ecosystem_uid in set(self.ecosystems_started):
                    self.stop_ecosystem(ecosystem_uid)
            if clear_engine:
                to_delete = set(self.ecosystems.keys())
                for ecosystem in to_delete:
                    self.dismount_ecosystem(ecosystem)

            self.logger.info("The Engine has stopped")
