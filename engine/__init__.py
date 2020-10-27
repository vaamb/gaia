# -*- coding: utf-8 -*-
import logging
import logging.config
import os
import requests
import json
from time import sleep
from threading import Thread, Event

from apscheduler.schedulers.background import BackgroundScheduler

from config import Config
from engine import config_parser
from engine.config_parser import getIds, configWatchdog, globalConfig, getConfig, createEcosystem, manageEcosystem, \
    delEcosystem, new_config_event, update as updateConfig
from engine.light import gaiaLight
from engine.sensors import gaiaSensors
from engine.health import gaiaHealth
from engine.climate import gaiaClimate


__all__ = ["autoManager", "enginesDict",
           "createEngine", "getEngine", "startEngine", "stopEngine", "delEngine",
           "gaiaEngine", "gaiaLight", "gaiaSensors", "gaiaHealth", "gaiaClimate",
           "createEcosystem", "manageEcosystem", "delEcosystem",
           "globalConfig", "getConfig", "updateConfig"]

SUBROUTINES = (gaiaLight, gaiaSensors, gaiaHealth, gaiaClimate)


# ---------------------------------------------------------------------------
#   Engine class
# ---------------------------------------------------------------------------
class gaiaEngine:
    """Create an Engine for a given ecosystem. 
    
    The Engine is an object that manages all the required subroutines. 
    IO intensive subroutines are launched in separate threads.

    User should use the module functions to interact with Engines
    rather than instantiate this class
    """

    def __init__(self, ecosystem):
        self._config = getConfig(ecosystem)
        self._ecosystem_id = self._config.ecosystem_id
        self._ecosystem_name = self._config.name
        self.logger = logging.getLogger(f"eng.{self._ecosystem_name}")

        self._started = False
        self._subroutines = {}
        self._alarms = []

    def start(self):
        if not self._started:
            self.logger.info("Starting Engine for ecosystem " +
                             f"{self._ecosystem_name}")
            configWatchdog.start()
            self._start_scheduler()
            threads = []
            # Initialize subroutines in thread as they are IO bound. After 
            # subroutines initialization is finished, all threads are deleted 
            # and IO-bound subroutines tasks are handled in their own thread.
            for subroutine in SUBROUTINES:  # add a check for subroutine management
                t = Thread(target=self._load_subroutine, args=(subroutine,))
                t.name = f"{subroutine.NAME}Loader-{self._ecosystem_id}"
                t.start()
                threads.append(t)
            # Save changes in config
            if not self.config_dict["status"]:
                self.config_dict["status"] = True

            self.logger.info(f"Engine for ecosystem {self._ecosystem_name} " +
                             "successfully started")
            for t in threads:
                t.join()
            del threads
            self._started = True
        else:
            print(f"Engine {self._ecosystem_name} is already running")

    def stop(self):
        self.logger.info("Stopping engine ...")
        self._stop_scheduler()
        self._started = False
        stopped_subroutines = []
        for subroutine in self._subroutines:
            subroutine_name = subroutine
            try:
                self._subroutines[subroutine].stop()
                self.logger.debug(f"{subroutine_name.capitalize()} " +
                                  "subroutine was stopped")
                stopped_subroutines.append(subroutine)
            except Exception as e:
                self.logger.error(f"{subroutine_name.capitalize()} subroutine " +
                                  f"was not shut down properly. ERROR msg: {e}")

        for subroutine in stopped_subroutines:
            self._subroutines.pop(subroutine)
        if not self._subroutines:
            if self.config_dict["status"]:
                self.config_dict["status"] = False
            # save changes in config
            self.logger.info("Engine stopped")
            return
        raise Exception

    def _load_subroutine(self, subroutine):
        try:
            self.logger.debug(f"Starting {subroutine.NAME} subroutine")
            self._subroutines[subroutine.NAME] = subroutine(self._ecosystem_id)
            self.logger.debug(f"{subroutine.NAME.capitalize()} subroutine " +
                              "successfully started")
        except Exception as e:
            self.logger.error(f"{subroutine.NAME.capitalize()} subroutine " +
                              f"was not successfully started. ERROR msg: {e}")

    def _start_scheduler(self):
        h, m = Config.HEALTH_LOGGING_TIME.split("h")
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(self._health_routine, trigger="cron",
                                hour=h, minute=m, misfire_grace_time=15 * 60,
                                id="health")
        self._scheduler.start()

    def _stop_scheduler(self):
        self.logger.info("Closing the tasks scheduler")
        self._scheduler.remove_job("health")
        self._scheduler.shutdown()
        del self._scheduler
        self.logger.info("The tasks scheduler was closed properly")

    def _health_routine(self):
        mode = self._subroutines["light"].mode or "automatic"
        status = self._subroutines["light"].status or False
        try:
            self.set_light_on()
            self._subroutines["health"].take_picture()
            if mode == "automatic":
                self.set_light_auto()
            else:
                if status:
                    self.set_light_on()
                else:
                    self.set_light_off()
        except KeyError:
            raise RuntimeError("Health and/or light subroutine is/are " +
                               f"not running in engine {self._ecosystem_name}")

    """API calls"""
    # Configuration info
    @property
    def name(self):
        return self._ecosystem_name

    @property
    def uid(self):
        return self._ecosystem_id

    @property
    def config_dict(self):
        return self._config.config_dict

    # Light
    def update_moments(self):
        subroutine = "light"
        try:
            self._subroutines[subroutine].update_moments()
        # The subroutine is not currently running
        except KeyError:
            raise RuntimeError(f"{subroutine.capitalize()} subroutine is " +
                               f"not running in engine {self._ecosystem_name}")

    @property
    def light_info(self):
        subroutine = "light"
        try:
            return self._subroutines[subroutine].light_info
        # The subroutine is not currently running
        except KeyError:
            return {"status": False,
                    "mode": "NA",
                    "method": self._config.light_method}

    def set_light_on(self, countdown=None):
        subroutine = "light"
        try:
            self._subroutines[subroutine].set_light_on(countdown=countdown)
        # The subroutine is not currently running
        except KeyError:
            raise RuntimeError(f"{subroutine.capitalize()} subroutine is " +
                               f"not running in engine {self._ecosystem_name}")

    def set_light_off(self, countdown=None):
        subroutine = "light"
        try:
            self._subroutines[subroutine].set_light_off(countdown=countdown)
        # The subroutine is not currently running
        except KeyError:
            raise RuntimeError(f"{subroutine.capitalize()} subroutine is " +
                               f"not running in engine {self._ecosystem_name}")

    def set_light_auto(self):
        subroutine = "light"
        try:
            self._subroutines[subroutine].set_light_auto()
        except KeyError:
            # The subroutine is not currently running
            raise RuntimeError(f"{subroutine.capitalize()} subroutine is " +
                               f"not running in engine {self._ecosystem_name}")

    # Sensors
    @property
    def sensors_data(self):
        subroutine = "sensors"
        try:
            return self._subroutines[subroutine].sensors_data
        except KeyError:
            # The subroutine is not currently running
            raise RuntimeError(f"{subroutine.capitalize()} subroutine is " +
                               f"not running in engine {self._ecosystem_name}")

    # Health
    @property
    def plants_health(self):
        subroutine = "health"
        try:
            return self._subroutines["health"].get_health_data()
        except KeyError:
            # The subroutine is not currently running
            raise RuntimeError(f"{subroutine.capitalize()} subroutine is " +
                               f"not running in engine {self._ecosystem_name}")

    # Get subroutines currently running
    @property
    def subroutine_running(self):
        return [subroutine.NAME for subroutine in self._subroutines]


# ---------------------------------------------------------------------------
#   Manager class
# ---------------------------------------------------------------------------
class Manager:
    """Create an Engine manager that will coordinate the Engines in case
    multiple engines are run on a single computer.

    Under normal circumstances only one Engine instance should be created
    for each ecosystem. The manager makes sure this is the case. The 
    manager is automatically instantiated at module load and should be
    accessed through module functions
    """

    def __init__(self):
        self.logger = logging.getLogger("eng.Manager")
        self.logger.debug("Starting the Engines Manager ...")
        self.engines = {}
        self.engines_started = []
        self._subroutine_dict = {}
        self._momentsManager = False
        self._scheduler = None
        self.autoManager = False
        self.managerThread = None
        self.stop_engines = False
        self.clear_manager = False

    def start_momentsManager(self):
        self.logger.debug("Starting the moments manager")
        self._scheduler = BackgroundScheduler()
        # No need to use ``_update_moments`` as no engine should have
        # started
        self.refresh_moments()  # put in in thread as it is IO bound
        self._scheduler.add_job(self.refresh_moments, "cron",
                                hour="1", misfire_grace_time=15 * 60,
                                id="moments")
        self._scheduler.start()
        self._momentsManager = True

    def stop_momentsManager(self):
        self.logger.debug("Shutting the moments manager")
        self._scheduler.remove_job("moments")
        self._scheduler.shutdown()
        self._momentsManager = False
        self._scheduler = None

    def _download_moments(self):
        # if at least one need moment and
        cache_dir = config_parser.gaiaEngine_dir / "cache"
        if not cache_dir:
            os.mkdir(cache_dir)
        if config_parser.is_connected():
            trials = 5
            latitude = globalConfig.home_coordinates["latitude"]
            longitude = globalConfig.home_coordinates["longitude"]
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
                    with open(cache_dir / "sunrise.cch", "w") as outfile:
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
        self.logger.error("gaiaEngine is not connected to the Internet, " +
                          "cannot download moments of the day")
        raise ConnectionError

    def refresh_moments(self):
        need = []
        for engine in self.engines:
            try:
                if globalConfig.config_dict[engine]["environment"]["light"] in ["place", "elongate"]:
                    need.append(engine)
            except KeyError:
                pass
        # return an exception NotConnected if not connected and exception NotRequired if no engine need it
        if need:
            try:
                # need to handle not connected now
                self._download_moments()
            except ConnectionError:
                pass
            for engine in need:
                try:
                    self.engines[engine].update_moments()
                except RuntimeError:
                    # engine created but light loop not started yet
                    pass
        else:
            print("No need to refresh moments")

    def createEngine(self, ecosystem, start=False):
        ecosystem_id, ecosystem_name = getIds(ecosystem)
        if ecosystem_id in self._subroutine_dict:
            self.stopSubroutine(ecosystem, "all")
        if ecosystem_id not in self.engines:
            engine = gaiaEngine(ecosystem)
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

    def stopEngine(self, ecosystem):
        ecosystem_id, ecosystem_name = getIds(ecosystem)
        if ecosystem_id in self.engines:
            if ecosystem_id in self.engines_started:
                engine = self.engines[ecosystem_id]
                engine.stop()
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

    def delEngine(self, ecosystem):
        ecosystem_id, ecosystem_name = getIds(ecosystem)
        if ecosystem_id in self.engines:
            if ecosystem_id in self.engines_started:
                self.logger.error("Cannot delete a started engine. " +
                                  "First need to stop it")
                return False
            else:
                del self.engines[ecosystem_id]
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


_manager = Manager()
enginesDict = _manager.engines


class autoManager:
    def __init__(self):
        self.logger = logging.getLogger("eng.autoManager")
        self.thread = None
        self.stop_engines = False
        self.clear_manager = False
        self._joiner = Event()
        self.started = False

    def loop(self):
        configWatchdog.start()
        while True:
            new_config_event.wait()
            # this happens when stopping autoManager
            if self.stop_engines:
                break
            expected_started = []
            to_delete = list(enginesDict.keys())
            for ecosystem in globalConfig.ecosystems_id:
                # create engine if it doesn't exist
                if ecosystem not in enginesDict:
                    _manager.createEngine(ecosystem)
                # remove the ecosystem from the to_delete_list
                else:
                    to_delete.remove(ecosystem)
                # check if the engine is expected to be running
                if globalConfig.status(ecosystem) is True:
                    expected_started.append(ecosystem)

            # start engines which are expected to run and are not running
            for ecosystem in expected_started:
                if ecosystem not in _manager.engines_started:
                    _manager.startEngine(ecosystem)
            # start engines which are not expected to run and are currently
            # running
            for ecosystem in _manager.engines_started:
                if ecosystem not in expected_started:
                    _manager.stopEngine(ecosystem)
            # delete engines which were created and are no longer on the
            # config file
            for ecosystem in to_delete:
                _manager.delEngine(ecosystem)
            new_config_event.clear()
            self._joiner.set()
        if self.stop_engines:
            for ecosystem in list(_manager.engines_started):
                _manager.stopEngine(ecosystem)
        if self.clear_manager:
            to_delete = list(enginesDict.keys())
            for ecosystem in to_delete:
                _manager.delEngine(ecosystem)
        sleep(10)

    def start(self, joint_start=False):
        if not self.started:
            _manager.logger.info("Starting the Engines autoManager ...")
            _manager.logger = self.logger

            self.thread = Thread(target=self.loop)
            self.thread.name = "autoManager"
            self.thread.start()
            # send a new config signal to fire the first loop
            new_config_event.set()
            if joint_start:
                self._joiner.wait()
            self.logger.info("Engines autoManager started")
            self.started = True
        else:
            raise RuntimeError("autoManager can only be started once")

    def start_join(self):
        if self.started:
            self._joiner.wait()

    def stop(self, stop_engines=True, clear_manager=True):
        if self.started:
            self.logger.info("Stopping the Engines autoManager ...")
            self.stop_engines = stop_engines
            if clear_manager:
                self.stop_engines = True
                self.clear_manager = True
            # send a new config signal so a last loops starts
            new_config_event.set()
            self.thread.join()
            self.thread = None
            self._joiner.clear()
            _manager.logger = logging.getLogger("eng.Manager")
            _manager.logger.info("autoManager stopped")
            self.started = False

    def status(self):
        return self.started


autoManager = autoManager()


# ---------------------------------------------------------------------------
#   Functions to interact with the module
# ---------------------------------------------------------------------------
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
    if not autoManager.started:
        return _manager.createEngine(ecosystem, start=start)
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
    if not autoManager.started:
        return _manager.getEngine(ecosystem, start=start)
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
    if not autoManager.started:
        return _manager.startEngine(ecosystem)
    raise Exception("You cannot manually manage engines while the " +
                    "autoManager is running")


def stopEngine(ecosystem):
    """Stop the engine for the specified ecosystem.
    
    :param ecosystem: The ecosystem id or name, as defined in the
                      ``ecosystems.cfg`` file.
    
    Return ``True`` if the engine and all its subroutines stopped
    correctly, ``False`` otherwise.
    """
    if not autoManager.started:
        return _manager.stopEngine(ecosystem)
    raise Exception("You cannot manually manage engines while the autoManager is running")


def delEngine(ecosystem):
    """Delete the engine for the specified ecosystem from the Manager
    internal dict.
    
    :param ecosystem: The ecosystem id or name, as defined in the
                      ``ecosystems.cfg`` file.
    
    Return ``True`` if the engine is deleted, ``False`` otherwise.
    """
    if not autoManager.started:
        return _manager.delEngine(ecosystem)
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
    if not autoManager.started:
        return _manager.createSubroutine(ecosystem, subroutine)
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
    if not autoManager.started:
        return _manager.stopSubroutine(ecosystem, subroutine)
    raise Exception("You cannot manually manage subroutines while the autoManager is running")


def subroutines_available():
    """
    Returns a list with all the subroutines available
    """
    return [subroutine.NAME for subroutine in SUBROUTINES]
