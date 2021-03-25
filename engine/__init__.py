from datetime import date, datetime
import hashlib
import json
import logging
import logging.config
import os
import requests
from threading import Thread, Event
from time import sleep

from apscheduler.schedulers.background import BackgroundScheduler
from socketio import Client

from engine import config_parser
from engine.climate import gaiaClimate
from engine.config_parser import getIds, configWatchdog, \
    getConfig, createEcosystem, manageEcosystem, delEcosystem, \
    new_config_event, updateConfig, delConfig
from engine.health import gaiaHealth
from engine.light import gaiaLight
from engine.sensors import gaiaSensors


__all__ = ["autoManager", "get_enginesDict", "inject_socketIO_client",
           "createEngine", "getEngine", "startEngine", "stopEngine", "delEngine",
           "Engine", "gaiaLight", "gaiaSensors", "gaiaHealth", "gaiaClimate",
           "createEcosystem", "manageEcosystem", "delEcosystem",
           "getConfig", "updateConfig"]

# TODO: keep specificConfig.get_subroutines() up to date
SUBROUTINES = (gaiaSensors, gaiaLight, gaiaClimate, gaiaHealth)


class _socketIO_proxy_class:
    def __init__(self):
        self._client = None
        self._enabled = False

    @property
    def client(self):
        return self._client

    @client.setter
    def client(self, socketIO_client):
        if isinstance(socketIO_client, Client):
            self._client = socketIO_client
            self._enabled = True
        else:
            print("socketIO_client must be an instance of socketio.Client")
    
    @client.deleter
    def client(self):
        # Not conventional but need to keep a trace of self._client
        self._client = None
        self._enabled = False

    @property
    def enabled(self):
        return self._enabled


_socketIO_proxy = _socketIO_proxy_class()


def inject_socketIO_client(socketIO_client):
    global _socketIO_proxy
    if not _socketIO_proxy.client:
        _socketIO_proxy.client = socketIO_client


# ---------------------------------------------------------------------------
#   Engine class
# ---------------------------------------------------------------------------
class Engine:
    """Create an Engine for a given ecosystem. 
    
    The Engine is an object that manages all the required subroutines. 
    IO intensive subroutines are launched in separate threads.

    User should use the module functions to interact with Engines
    rather than instantiate this class
    """

    def __init__(self, ecosystem):
        self._config = getConfig(ecosystem)
        self._ecosystem_uid = self._config.ecosystem_id
        self._ecosystem_name = self._config.name
        self.logger = logging.getLogger(f"eng.{self._ecosystem_name}")
        self.logger.info(f"Initializing Engine")

        self._alarms = []
        self.subroutines = {}
        try:
            for subroutine in SUBROUTINES:
                self.subroutines[subroutine.NAME] = subroutine(engine=self)
        except Exception as e:
            self.logger.error("Error during Engine initialization. " +
                              f"ERROR msg: {e}")
        self._started = False
        self.logger.debug(f"Engine initialization successful")

    # TODO: maybe add id(self) in the hash?
    def __eq__(self, other):
        h = hashlib.sha256()
        h.update(json.dumps(self.config, sort_keys=True).encode())
        h.update(json.dumps(self.subroutines_started, sort_keys=True).encode())
        return h.digest() == other

    @property
    def _socketIO_client(self):
        return _socketIO_proxy.client

    @property
    def _socketIO_enabled(self):
        return _socketIO_proxy.enabled

    def start(self):
        if not self._started:
            self.logger.info("Starting Engine")
            if not configWatchdog.status():
                configWatchdog.start()

            # Start subroutines in thread as they are IO bound. After
            # subroutines initialization is finished, all threads are deleted 
            # and IO-bound subroutines tasks are handled in their own thread.
            threads = []
            for subroutine in self._config.get_started_subroutines():
                t = Thread(target=self._start_subroutine, args=(subroutine, ))
                t.name = f"{self._ecosystem_uid}-{subroutine}Starter"
                t.start()
                threads.append(t)
            if not self.config["status"]:
                # TODO: save changes in config?
                # (for now, status refers to whether Engine SHOULD be on or not)
                self.config["status"] = True
            try:
                for t in threads:
                    t.join()
            except Exception as e:
                self._logger.error(
                    f"Engine was not successfully started. ERROR msg: {e}")
                raise e
            self.logger.debug(f"Engine successfully started")
            del threads
            self._started = True
        else:
            raise RuntimeError(f"Engine {self._ecosystem_name} is already running")

    def stop(self):
        if self._started:
            self.logger.info("Stopping engine ...")
            for subroutine in [i for i in self.subroutines.keys()]:
                self.subroutines[subroutine].stop()
            if not any([self.subroutines[subroutine].status
                        for subroutine in self.subroutines]):
                if self.config["status"]:
                    self.config["status"] = False
                # TODO: save changes in config
                self.logger.debug("Engine successfully stopped")
            else:
                self.logger.error("Failed to stop Engine")
                raise Exception(f"Failed to stop Engine for {self._ecosystem_name}")
            self._started = False

    def _start_subroutine(self, subroutine):
        self.subroutines[subroutine].start()

    """API calls"""
    # Configuration info
    @property
    def name(self):
        return self._ecosystem_name

    @property
    def uid(self):
        return self._ecosystem_uid

    @property
    def status(self):
        return self._started

    @property
    def config(self):
        # TODO: change sensors, light ... management to check whether hardware is used
        return self._config.config_dict

    # Light
    def update_sun_times(self):
        self.subroutines["light"].update_sun_times()

    @property
    def light_info(self):
        return self.subroutines["light"].light_info

    def turn_light(self, mode="automatic", countdown=None):
        try:
            self.subroutines["light"].turn_light(mode=mode, countdown=countdown)
        # The subroutine is not currently running
        except RuntimeError as e:
            self.logger.error(e)

    # Sensors
    @property
    def sensors_data(self):
        return self.subroutines["sensors"].sensors_data

    # Health
    @property
    def plants_health(self):
        return self.subroutines["health"].health_data

    # Get subroutines currently running
    @property
    def subroutines_started(self):
        return [subroutine for subroutine in self.subroutines
                if self.subroutines[subroutine].status]


# ---------------------------------------------------------------------------
#   _enginesManager class
# ---------------------------------------------------------------------------
class _enginesManager:
    """Create an Engine manager that will coordinate the Engines in case
    multiple Engines are run on a single computer.

    Under normal circumstances only one Engine instance should be created
    for each ecosystem. The manager makes sure this is the case. The 
    manager is automatically instantiated if needed and should be
    accessed through module functions (cf bottom of the file).
    """

    def __init__(self):
        self.logger = logging.getLogger("eng.Manager")
        self.logger.debug("Starting the Engines Manager ...")
        self.engines = {}
        self.engines_started = []
        self._subroutine_dict = {}
        self._momentsManager = False
        self._scheduler = None
        self.managerThread = None
        self.stop_engines = False
        self.clear_manager = False

    # TODO: check startup without internet
    def start_momentsManager(self):
        self.logger.debug("Starting the sun_times manager")
        self._scheduler = BackgroundScheduler()
        self.refresh_sun_times()
        self._scheduler.add_job(self.refresh_sun_times, "cron",
                                hour="1", misfire_grace_time=15 * 60,
                                id="sun_times")
        self._scheduler.start()
        self._momentsManager = True

    def stop_momentsManager(self):
        self.logger.debug("Shutting the sun_times manager")
        self._scheduler.remove_job("sun_times")
        self._scheduler.shutdown()
        self._momentsManager = False
        self._scheduler = None

    def _download_sun_times(self):
        global_config = getConfig()
        cache_dir = config_parser.base_dir / "cache"
        if not cache_dir.exists():
            os.mkdir(cache_dir)

        # Determine if the file needs to be updated
        need_update = True
        try:
            update_epoch = cache_dir.stat().st_ctime
            update_dt = datetime.fromtimestamp(update_epoch)
        except FileNotFoundError:
            need_update = True

        if update_dt.date() >= date.today():
            need_update = False
            self.logger.info("Sun times already up to date")

        if not config_parser.is_connected():
            self.logger.error("gaiaEngine is not connected to the Internet, " +
                              "cannot download sun_times of the day")
            raise ConnectionError

        if need_update:
            trials = 5
            latitude = global_config.home_coordinates["latitude"]
            longitude = global_config.home_coordinates["longitude"]
            count = 1
            while True:
                try:
                    self.logger.info("Trying to update sunrise and sunset " +
                                     "times on sunrise-sunset.org " +
                                     f"-- trial {count}/{trials}")
                    data = requests.get("https://api.sunrise-sunset.org/json?lat="
                                        + str(latitude) + "&lng=" + str(longitude)).json()
                    results = data["results"]
                except ConnectionError:
                    pass
                else:
                    with open(cache_dir / "sunrise.json", "w") as outfile:
                        json.dump(results, outfile)
                    self.logger.info("Sunrise and sunset times successfully " +
                                     "updated")
                    break
                if count < trials:
                    if count < trials - 1:
                        self.logger.info("Failed to update sunrise and sunset " +
                                         "times, retrying")
                    sleep(0.25)
                    continue
                else:
                    self.logger.error("Failed to update sunrise and " +
                                      "sunset times")
                    raise ConnectionError

    def refresh_sun_times(self):
        need = []
        for engine in self.engines:
            try:
                if self.engines[engine].config["environment"]["light"] in ("mimic", "elongate"):
                    need.append(engine)
            except KeyError:
                pass
        # return an exception NotConnected if not connected and exception NotRequired if no engine need it
        if any(need):
            try:
                # need to handle not connected now
                self._download_sun_times()
            except ConnectionError:
                for engine in need:
                    self.engines[engine].config["environment"]["light"] = "fixed"
                # TODO: make retry possible after a first error
                self.logger.warning("gaiaEngine could not download sun times."
                                    "engines light mode has been turned to 'fixed'")
            else:
                for engine in need:
                    try:
                        self.engines[engine].update_sun_times()
                    # Except if engine not initialized yet
                    except KeyError:
                        pass
        else:
            self.logger.info("No need to refresh sun_times")

    def createEngine(self, ecosystem, start=False):
        ecosystem_id, ecosystem_name = getIds(ecosystem)
        if ecosystem_id in self._subroutine_dict:
            self.stopSubroutine(ecosystem, "all")
        if ecosystem_id not in self.engines:
            engine = Engine(ecosystem)
            self.engines[ecosystem_id] = engine
            self.logger.info(f"Engine for ecosystem {ecosystem_name} has " +
                             "been created")
            if start:
                self.startEngine(ecosystem_id)
            return engine
        self.logger.debug(f"Engine for ecosystem {ecosystem_name} already " +
                          "exists")
        return False

    def getEngine(self, ecosystem, start=False):
        ecosystem_id, ecosystem_name = getIds(ecosystem)
        if ecosystem_id in self.engines:
            engine = self.engines[ecosystem_id]
        else:
            engine = self.createEngine(ecosystem_id, start=start)
        return engine

    def startEngine(self, ecosystem):
        ecosystem_id, ecosystem_name = getIds(ecosystem)
        if ecosystem_id in self.engines:
            if not self.engines_started:
                configWatchdog.start()
                self.start_momentsManager()
            if ecosystem_id not in self.engines_started:
                engine = self.engines[ecosystem_id]
                self.logger.info("Starting engine for ecosystem " +
                                 f"{ecosystem_name}")
                engine.start()
                self.engines_started.append(ecosystem_id)
                self.logger.info(f"Engine for ecosystem {ecosystem_name} " +
                                 "started")
                return True
            else:
                self.logger.debug(f"Engine for ecosystem {ecosystem_name} " +
                                  "has already been started")
                return True
        self.logger.warning(f"Engine for ecosystem {ecosystem_name} has " +
                            "not been created yet")
        return False

    def stopEngine(self, ecosystem, clean=False):
        ecosystem_id, ecosystem_name = getIds(ecosystem)
        if ecosystem_id in self.engines:
            if ecosystem_id in self.engines_started:
                engine = self.engines[ecosystem_id]
                engine.stop()
                if clean:
                    self.delEngine(ecosystem_id)
                self.engines_started.remove(ecosystem_id)
                self.logger.info(f"Engine for ecosystem {ecosystem_name} " +
                                 "has been stopped")
                # If no more engines running, stop background routines
                if not self.engines_started:
                    configWatchdog.stop()
                    self.stop_momentsManager()
                return True
            else:
                self.logger.warning("Cannot stop engine for ecosystem " +
                                    f"{ecosystem_name} as it has not been " +
                                    "started yet")
                return False
        else:
            self.logger.warning("Cannot stop engine for ecosystem " +
                                f"{ecosystem_name} as it does not exist")
            return False

    def delEngine(self, ecosystem, delete_config: bool = True):
        ecosystem_id, ecosystem_name = getIds(ecosystem)
        if ecosystem_id in self.engines:
            if ecosystem_id in self.engines_started:
                self.logger.error("Cannot delete a started engine. " +
                                  "First need to stop it")
                return False
            else:
                # extra security because of circular reference
                for subroutine in [subroutine for subroutine in
                                   self.engines[ecosystem_id].subroutines.keys()]:
                    del self.engines[ecosystem_id].subroutines[subroutine]
                del self.engines[ecosystem_id]
                if delete_config:
                    delConfig(ecosystem_id)
                self.logger.info(f"Engine for ecosystem {ecosystem_name} " +
                                 "has been deleted")
                return True
        else:
            self.logger.warning("Cannot delete engine for ecosystem " +
                                f"{ecosystem_name} as it does not exist")
            return False

    def createSubroutine(self, ecosystem, subroutine_name):
        ecosystem_id, ecosystem_name = getIds(ecosystem)
        if subroutine_name not in [subroutine.NAME for subroutine in SUBROUTINES]:
            print(f"Subroutine '{subroutine_name}' is not available. Use " +
                  "'subroutines_available()' to see available subroutine names")
            return False
        if ecosystem_id in self.engines:
            self.logger.warning("You cannot create a subroutine for " +
                                f"{ecosystem_name} if its engine is " +
                                "already running")
            return False
        if ecosystem_id in self._subroutine_dict:
            module = self._subroutine_dict[ecosystem_id].get(subroutine_name, False)
            if module:
                self.logger.debug(f"{subroutine_name.capitalize()} " +
                                  "subroutine is already running for " +
                                  f"ecosystem {ecosystem_name}")
                return True
            if not module:
                for subroutine in SUBROUTINES:
                    if subroutine.NAME == subroutine_name:
                        self._subroutine_dict[ecosystem_id] = {subroutine_name: subroutine(ecosystem_id)}
                        self.logger.info(f"{subroutine_name.capitalize()} " +
                                         "subroutine created for ecosystem " +
                                         f"{ecosystem_name}")
                        return True
        else:
            for subroutine in SUBROUTINES:
                if subroutine.NAME == subroutine_name:
                    self._subroutine_dict[ecosystem_id] = {subroutine_name: subroutine(ecosystem_id)}
                    self.logger.debug(f"{subroutine_name.capitalize()}  " +
                                      "subroutine created for ecosystem " +
                                      f"{ecosystem_name}")
                    return True

    def stopSubroutine(self, ecosystem, subroutine_name):
        ecosystem_id, ecosystem_name = getIds(ecosystem)
        if subroutine_name not in [subroutine.NAME for subroutine in SUBROUTINES]:
            print(f"Subroutine '{subroutine_name}' is not available. Use " +
                  "'subroutines_available()' to see available subroutine")
            return False
        if subroutine_name == "all":
            for subroutine in self._subroutine_dict[ecosystem_id]:
                self._subroutine_dict[ecosystem_id][subroutine].stop()
            self.logger.info("All subroutines have been stopped for ecosystem" +
                             "{ecosystem_name}")
            return True
        try:
            self._subroutine_dict[ecosystem_id][subroutine_name].stop()
            self.logger.info(f"{subroutine_name.capitalize()} subroutine " +
                             f"has been stopped for ecosystem {ecosystem_name}")
            return True
        except KeyError:
            self.logger.warning(f"Cannot stop {subroutine_name} subroutine for " +
                                f"ecosystem {ecosystem_name} as it does not exist")
            return False


_engines_manager = None


def get_enginesDict() -> dict:
    global _engines_manager
    if not _engines_manager:
        _engines_manager = _enginesManager()
    return _engines_manager.engines


# ---------------------------------------------------------------------------
#   _autoManager class
# ---------------------------------------------------------------------------
class _autoManager:
    def __init__(self):
        """ Auto start and stops Engines based on configuration files.

        This class should be accessed through the dummy class autoManager which
        makes sure only one instance of _autoManager is created.
        """
        self.logger = logging.getLogger("eng.autoManager")
        self._thread = None
        self.stop_engines = False
        self.clear_manager = False
        self._joiner = Event()
        self._started = False

    def _loop(self):
        global_config = getConfig()
        while True:
            new_config_event.wait()
            # this happens when stopping autoManager
            if self.stop_engines:
                break
            expected_started = []
            to_delete = list(_engines_manager.engines.keys())
            for ecosystem in global_config.ecosystems_id:
                # create engine if it doesn't exist
                if ecosystem not in _engines_manager.engines:
                    _engines_manager.createEngine(ecosystem)
                # remove the ecosystem from the to_delete_list
                else:
                    to_delete.remove(ecosystem)
                # check if the engine is expected to be running
                if global_config.status(ecosystem) is True:
                    expected_started.append(ecosystem)

            # start engines which are expected to run and are not running
            for ecosystem in expected_started:
                if ecosystem not in _engines_manager.engines_started:
                    _engines_manager.startEngine(ecosystem)
            # start engines which are not expected to run and are currently
            # running
            for ecosystem in _engines_manager.engines_started:
                if ecosystem not in expected_started:
                    _engines_manager.stopEngine(ecosystem)
            # delete engines which were created and are no longer on the
            # config file
            for ecosystem in to_delete:
                _engines_manager.delEngine(ecosystem)
            new_config_event.clear()
            self._joiner.set()
        if self.stop_engines:
            for ecosystem in list(_engines_manager.engines_started):
                _engines_manager.stopEngine(ecosystem)
        if self._engines_manager:
            to_delete = list(_engines_manager.engines.keys())
            for ecosystem in to_delete:
                _engines_manager.delEngine(ecosystem)

    def start(self, joint_start=False):
        if not self._started:
            global _engines_manager
            if not _engines_manager:
                _engines_manager = _enginesManager()

            _engines_manager.logger.info("Starting the Engines autoManager ...")
            _engines_manager.logger = self.logger

            self._thread = Thread(target=self._loop)
            self._thread.name = "autoManager"
            self._thread.start()
            # send a new config signal to fire the first loop
            new_config_event.set()
            if joint_start:
                self._joiner.wait()
            self.logger.info("Engines autoManager started")
            self._started = True
        else:
            raise RuntimeError("autoManager can only be started once")

    def start_join(self):
        if self._started:
            self._joiner.wait()

    def stop(self, stop_engines=True, clear_manager=True):
        if self._started:
            self.logger.info("Stopping the Engines autoManager ...")
            self.stop_engines = stop_engines
            if clear_manager:
                self.stop_engines = True
                self.clear_manager = True
            # send a new config signal so a last loops starts
            new_config_event.set()
            self._thread.join()
            self._thread = None
            self._joiner.clear()
            _engines_manager.logger = logging.getLogger("eng.Manager")
            _engines_manager.logger.info("autoManager stopped")
            self._started = False

    @property
    def status(self):
        return self._started


_auto_manager = None


# ---------------------------------------------------------------------------
#   Classe and functions to interact with the module
# ---------------------------------------------------------------------------
class autoManager:
    """ Dummy class to interact with _autoManager()

    This class will instantiate _autoManager() if needed and allow to interact
    with it. This allows not to launch an instance of _autoManager when loading
    this module.
    """

    @staticmethod
    def start(joint_start=False) -> None:
        global _auto_manager
        if not _auto_manager:
            _auto_manager = _autoManager()
        _auto_manager.start(joint_start=joint_start)

    @staticmethod
    def stop(stop_engines=True, clear_manager=True) -> bool:
        global _auto_manager
        if _auto_manager:
            _auto_manager.stop(stop_engines=stop_engines,
                               clear_manager=clear_manager)

    @staticmethod
    def status() -> bool:
        global _auto_manager
        if not _auto_manager:
            return False
        return _auto_manager.status

    @staticmethod
    def is_init() -> bool:
        global _auto_manager
        if not _auto_manager:
            return False
        return True


def createEngine(ecosystem, start=False):
    """Create an engine for the specified ecosystem.
    
    :param ecosystem: The ecosystem id or name, as defined in the
                      ``ecosystems.cfg`` file.
    :param start: If ``False``, the Engine will not start after being
                  instantiated. If ``True``, the engine will start its
                  subroutines after instantiation. Default to ``False``
    
    Return an Engine object if the Engine and all its subroutines was
    correctly created, ``False`` otherwise or if the Engine already
    existed for the given ecosystem.
    
    Rem: cannot be used if the autoManager has been started.
    """
    if not autoManager.status():
        global _engines_manager
        if not _engines_manager:
            _engines_manager = _enginesManager()
        return _engines_manager.createEngine(ecosystem, start=start)
    raise Exception("You cannot manually manage engines while the " +
                    "autoManager is running")


def getEngine(ecosystem, start=False):
    """Returns the engine for the specified ecosystem.
    
    :param ecosystem: The ecosystem id or name, as defined in the
                      ``ecosystems.cfg`` file.
    :param start: If ``False``, the Engine will not start after being
                  instantiated. If ``True``, the engine will start its
                  subroutines after instantiation. Default to ``False``

    Return the required Engine object if if exists. If it does not
    exist, the required Engine will be created and returned.
    
    Rem: cannot be used if the autoManager has been started.
    """
    if not autoManager.status():
        global _engines_manager
        if not _engines_manager:
            _engines_manager = _enginesManager()
        return _engines_manager.getEngine(ecosystem, start=start)
    raise Exception("You cannot manually manage engines while the " +
                    "autoManager is running")


def startEngine(ecosystem):
    """Start the engine for the specified ecosystem. 
    
    :param ecosystem: The ecosystem id or name, as defined in the
                      ``ecosystems.cfg`` file.

    Return ``True`` is the engine as been properly started or is already
    running, ``False`` if the Engine has not been created yet.
    
    Rem: cannot be used if the autoManager has been started.
    """
    if not autoManager.status():
        global _engines_manager
        if not _engines_manager:
            _engines_manager = _enginesManager()
        return _engines_manager.startEngine(ecosystem)
    raise Exception("You cannot manually manage engines while the " +
                    "autoManager is running")


def stopEngine(ecosystem):
    """Stop the engine for the specified ecosystem.
    
    :param ecosystem: The ecosystem id or name, as defined in the
                      ``ecosystems.cfg`` file.
    
    Return ``True`` if the engine and all its subroutines stopped
    correctly, ``False`` otherwise.
    """
    if not autoManager.status():
        global _engines_manager
        if not _engines_manager:
            return False
        return _engines_manager.stopEngine(ecosystem, clean=False)
    raise Exception("You cannot manually manage engines while the autoManager is running")


def delEngine(ecosystem, delete_config: bool = True):
    """Delete the engine for the specified ecosystem from the Manager
    internal dict.
    
    :param ecosystem: The ecosystem id or name, as defined in the
                      ``ecosystems.cfg`` file.
    
    Return ``True`` if the engine is deleted, ``False`` otherwise.
    """
    if not autoManager.status():
        global _engines_manager
        if not _engines_manager:
            _engines_manager = _enginesManager()
        return _engines_manager.delEngine(ecosystem, delete_config)
    raise Exception("You cannot manually manage engines while the autoManager is running")


def createSubroutine(ecosystem, subroutine):
    """Create and start a subroutine following the configuration for the
    given ecosystem.
    
    :param ecosystem: The ecosystem id or name, as defined in the
                      ``ecosystems.cfg`` file.
    :param subroutine: A subroutine name. Full list of subroutines can
                       be obtained by using ``subroutines_available()``
        
    Return ``True`` is the subroutine was correctly created and started,
    ``False`` otherwise.
    
    Rem: cannot be used if the autoManager has been started.
    """
    if not autoManager.status():
        global _engines_manager
        if not _engines_manager:
            _engines_manager = _enginesManager()
        return _engines_manager.createSubroutine(ecosystem, subroutine)
    raise Exception("You cannot manually manage subroutines while the autoManager is running")


def stopSubroutine(ecosystem, subroutine):
    """Stop the subroutine that follows the configuration for the
    given ecosystem.
    
    :param ecosystem: The ecosystem id or name, as defined in the
                      ``ecosystems.cfg`` file.
    :param subroutine: A subroutine name. Full list of subroutines can
                       be obtained by using ``subroutines_available()``
        
    Return ``True`` is the subroutine was correctly stopped, ``False``
    otherwise.
    
    Rem: cannot be used if the autoManager has been started.
    """
    if not autoManager.status():
        global _engines_manager
        if not _engines_manager:
            _engines_manager = _enginesManager()
        return _engines_manager.stopSubroutine(ecosystem, subroutine)
    raise Exception("You cannot manually manage subroutines while the autoManager is running")


def subroutines_available():
    """
    Returns a list with all the subroutines available
    """
    return [subroutine.NAME for subroutine in SUBROUTINES]
