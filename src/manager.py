from datetime import date, datetime
import json
import logging
import logging.config
import os
import requests
from threading import Thread, Event

from socketio import Client
from typing import Dict

from config import Config
from .config_parser import config_event, detach_config, get_config,\
    get_IDs, get_general_config
from .engine import Engine
from .shared_resources import scheduler, start_scheduler
from .utils import base_dir, SingletonMeta
from .virtual import get_virtual_ecosystem


class enginesManager(metaclass=SingletonMeta):
    """Create an Engine manager that will coordinate the Engines in case
    multiple Engines are run on a single computer.

    Under normal circumstances only one Engine instance should be created
    for each ecosystem. The manager makes sure this is the case. The
    manager is automatically instantiated if needed and should be
    accessed through module functions (cf bottom of the file).
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("eng.Manager")
        self.logger.debug("Starting the Engines Manager ...")
        self.engines: Dict[str, Engine] = {}
        self.engines_started: list = []
        self._start_joiner: Event = Event()
        self._run: bool = False
        self._thread: Thread = None
        self._socketIO_client: Client = None
        self._last_sun_times_update = None

    # TODO: check startup without internet
    def start_background_tasks(self) -> None:
        self.logger.debug("Starting background tasks")
        get_general_config().start_watchdog()
        cache_dir = base_dir/"cache"
        if not cache_dir.exists():
            os.mkdir(cache_dir)
        self.refresh_sun_times()
        scheduler.add_job(self.refresh_sun_times, "cron",
                          hour="1", misfire_grace_time=15 * 60,
                          id="sun_times")
        start_scheduler()

    def stop_background_tasks(self) -> None:
        self.logger.debug("Stopping background tasks")
        get_general_config().stop_watchdog()
        scheduler.remove_job("sun_times")

    def _download_sun_times(self) -> None:
        global_config = get_config()
        save_file = base_dir/"cache"/"sunrise.json"

        # Determine if the file needs to be updated
        need_update = True
        try:
            self._last_sun_times_update = save_file.stat().st_mtime
            self._last_sun_times_update = datetime.fromtimestamp(
                self._last_sun_times_update)

        except FileNotFoundError:
            need_update = True
        else:
            if self._last_sun_times_update.date() == date.today():
                need_update = False
                self.logger.debug("Sun times already up to date")

        if need_update:
            latitude = global_config.home_coordinates["latitude"]
            longitude = global_config.home_coordinates["longitude"]
            try:
                self.logger.debug(
                    "Trying to update sunrise and sunset times on "
                    "sunrise-sunset.org"
                )
                response = requests.get(
                    url=f"https://api.sunrise-sunset.org/json",
                    params={"lat": latitude, "lng": longitude},
                    timeout=3.0
                )
                data = response.json()
                results = data["results"]
            except requests.exceptions.ConnectionError:
                self.logger.debug(
                    "Failed to update sunrise and sunset times"
                )
                raise ConnectionError
            else:
                with open(save_file, "w") as outfile:
                    json.dump(results, outfile)
                self.logger.debug(
                    "Sunrise and sunset times successfully updated"
                )

    def refresh_sun_times(self) -> None:
        self.logger.debug("Check if sun times need to be refreshed")
        need = []
        for engine in self.engines:
            try:
                if (self.engines[engine].config["environment"]["light"] in
                        ("mimic", "elongate")):
                    need.append(engine)
            except KeyError:
                # Bad configuration file
                pass
        if any(need):
            try:
                self.logger.info("Refreshing sun times")
                self._download_sun_times()
            except ConnectionError:
                self.logger.error("gaiaEngine could not download sun times")
                # TODO: find a better way
                for engine in need:
                    self.engines[engine].config["environment"]["light"] = "fixed"
            else:
                for engine in need:
                    try:
                        self.engines[engine].update_sun_times()
                    except KeyError:
                        # Occur
                        pass
        else:
            self.logger.debug("No need to refresh sun times")

    @property
    def last_sun_times_update(self):
        return self._last_sun_times_update

    # TODO: handle creation of multiple engines, put a joint option
    def create_engine(self, ecosystem: str, start: bool = False) -> Engine:
        ecosystem_uid, ecosystem_name = get_IDs(ecosystem)
        if ecosystem_uid not in self.engines:
            engine = Engine(ecosystem_uid, self)
            self.engines[ecosystem_uid] = engine
            self.logger.debug(
                f"Engine for ecosystem {ecosystem_name} has been created"
            )
            if start:
                self.start_engine(ecosystem_uid)
            return engine
        raise RuntimeError(
            f"Engine for ecosystem {ecosystem_name} already exists"
        )

    def get_engine(self, ecosystem: str, start: bool = False) -> Engine:
        ecosystem_uid, ecosystem_name = get_IDs(ecosystem)
        if ecosystem_uid in self.engines:
            engine = self.engines[ecosystem_uid]
        else:
            engine = self.create_engine(ecosystem_uid, start=start)
        return engine

    def start_engine(self, ecosystem: str) -> bool:
        ecosystem_uid, ecosystem_name = get_IDs(ecosystem)
        if ecosystem_uid in self.engines:
            if not self.engines_started:
                self.start_background_tasks()
            if ecosystem_uid not in self.engines_started:
                engine = self.engines[ecosystem_uid]
                self.logger.debug(
                    f"Starting engine for ecosystem {ecosystem_name}"
                )
                engine.start()
                self.engines_started.append(ecosystem_uid)
                self.logger.info(
                    f"Engine for ecosystem {ecosystem_name} started"
                )
                return True
            else:
                self.logger.debug(f"Engine for ecosystem {ecosystem_name} " +
                                  f"has already been started")
                return True
        self.logger.warning(f"Engine for ecosystem {ecosystem_name} has " +
                            f"not been created yet")
        return False

    def stop_engine(self, ecosystem: str, clean: bool = False) -> bool:
        ecosystem_uid, ecosystem_name = get_IDs(ecosystem)
        if ecosystem_uid in self.engines:
            if ecosystem_uid in self.engines_started:
                engine = self.engines[ecosystem_uid]
                engine.stop()
                if clean:
                    self.del_engine(ecosystem_uid)
                self.engines_started.remove(ecosystem_uid)
                self.logger.info(f"Engine for ecosystem {ecosystem_name} " +
                                 f"has been stopped")
                # If no more engines running, stop background routines
                if not self.engines_started:
                    self.stop_background_tasks()
                return True
            else:
                self.logger.warning(f"Cannot stop engine for ecosystem " +
                                    f"{ecosystem_name} as it has not been " +
                                    f"started yet")
                return False
        else:
            self.logger.warning(f"Cannot stop engine for ecosystem " +
                                f"{ecosystem_name} as it does not exist")
            return False

    def del_engine(self, ecosystem: str, detatch_config: bool = True) -> bool:
        ecosystem_id, ecosystem_name = get_IDs(ecosystem)
        if ecosystem_id in self.engines:
            if ecosystem_id in self.engines_started:
                self.logger.error("Cannot delete a started engine. " +
                                  "First need to stop it")
                return False
            else:
                del self.engines[ecosystem_id]
                if detatch_config:
                    detach_config(ecosystem_id)
                self.logger.info(f"Engine for ecosystem {ecosystem_name} " +
                                 f"has been deleted")
                return True
        else:
            self.logger.warning(f"Cannot delete engine for ecosystem " +
                                f"{ecosystem_name} as it does not exist")
            return False

    def _loop(self) -> None:
        global_config = get_config()
        while self._run:
            expected_started = []
            to_delete = list(self.engines.keys())
            if Config.VIRTUALIZATION:
                for ecosystem_uid in global_config.ecosystems_uid:
                    get_virtual_ecosystem(ecosystem_uid, start=True)
            for ecosystem in global_config.ecosystems_uid:
                # create engine if it doesn't exist
                if ecosystem not in self.engines:
                    self.create_engine(ecosystem)
                # remove the ecosystem from the to_delete_list
                else:
                    to_delete.remove(ecosystem)
                # check if the engine is expected to be running
                if global_config.status(ecosystem):
                    expected_started.append(ecosystem)

            # start engines which are expected to run and are not running
            for ecosystem in expected_started:
                if ecosystem not in self.engines_started:
                    self.start_engine(ecosystem)
            # start engines which are not expected to run and are currently
            # running
            for ecosystem in self.engines_started:
                if ecosystem not in expected_started:
                    self.stop_engine(ecosystem)
            # delete engines which were created and are no longer on the
            # config file
            for ecosystem in to_delete:
                self.del_engine(ecosystem)
            self._start_joiner.set()
            with config_event:
                config_event.wait()
            for ecosystem_uid in self.engines:
                try:
                    self.engines[ecosystem_uid].update_sun_times(send=False)
                except KeyError:
                    pass
            if self.socketIO_client:
                self.socketIO_client.namespace_handlers[
                    "/gaia"].on_send_config()
                self.socketIO_client.namespace_handlers[
                    "/gaia"].on_send_light_data()

    def start(self, joint_start: bool = False) -> None:
        if not self._run:
            self.logger.info("Starting the Engines autoManager ...")
            self._run = True
            self._thread = Thread(target=self._loop)
            self._thread.name = "autoManager"
            self._thread.start()
            if joint_start:
                self._start_joiner.wait()
            self.logger.info("Engines autoManager started")
        else:
            raise RuntimeError("autoManager can only be started once")

    def stop(self, stop_engines: bool = True,
             clear_manager: bool = True) -> None:
        if self._run:
            self.logger.info("Stopping the Engines autoManager ...")
            if clear_manager:
                stop_engines = True
            self._run = False
            # send a config signal so a last loops starts
            with config_event:
                config_event.notify_all()
            self._thread.join()
            self._thread = None
            self._start_joiner.clear()

            if stop_engines:
                for ecosystem in list(self.engines_started):
                    self.stop_engine(ecosystem)
            if clear_manager:
                to_delete = list(self.engines.keys())
                for ecosystem in to_delete:
                    self.del_engine(ecosystem)

            self.logger.info("The Engines autoManager has stopped")

    @property
    def socketIO_client(self):
        return self._socketIO_client

    @socketIO_client.setter
    def socketIO_client(self, socketIO_client):
        if isinstance(socketIO_client, Client):
            self._socketIO_client = socketIO_client
        else:
            raise TypeError(
                "socketIO_client must be an instance of socketio.Client"
            )
