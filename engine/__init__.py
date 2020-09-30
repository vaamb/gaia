#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
import logging.config
import requests
import json
from time import sleep
from threading import Thread
import sys

from apscheduler.schedulers.background import BackgroundScheduler

from . import config
from .config import globalConfig, getConfig, createEcosystem, manageEcosystem,\
    delEcosystem, new_config, update as updateConfig
from .light import gaiaLight
from .sensors import gaiaSensors
from .health import gaiaHealth
from .climate import gaiaClimate

if "client" in sys.modules:
    from client import sio
    CLIENT = True
else:
    CLIENT = False

def load_client():
    from client import sio
    CLIENT = True

logging.config.dictConfig(config.LOGGING_CONFIG)

__all__ = ["createEngine", "getEngine", "startEngine", "stopEngine", "delEngine",
           "gaiaEngine", "gaiaLight", "gaiaSensors","gaiaHealth", "gaiaClimate",
           "createEcosystem", "manageEcosystem", "delEcosystem",
           "globalConfig", "getConfig", "updateConfig"]

SUBROUTINES = (gaiaLight, gaiaSensors, gaiaHealth, gaiaClimate)

DEBUG = False



#---------------------------------------------------------------------------
#   engine class and functions
#---------------------------------------------------------------------------
class gaiaEngine():
    """Create an Engine for a given ecosystem. 
    
    The Engine is an object that manages all the required subroutines. 
    IO intensive subroutines are launched in separate threads.

    User should use the module functions to interact with Engines
    rather than instanciate this class
    """
    def __init__(self, ecosystem): 
        self._config = getConfig(ecosystem)
        self._ecosystem_id = self._config.ecosystem_id
        self._ecosystem_name = self._config.name
        self._logger = logging.getLogger(f"eng.{self._ecosystem_name}")

        self._run = False
        self._subroutines = {}
        self._alarms = []

    def start(self, wait_join=False):
        if not self._run:
            self._logger.info("Starting Engine for ecosystem " +
                              f"{self._ecosystem_name}")
            config.start_watchdog()
            self._start_scheduler()
            threads = []
            # Initialize subroutines in thread as they are IO bound. After 
            # subroutines initialization is finished, all threads are deleted 
            # and IO-bound subroutines tasks are handled in their own thread.
            for subroutine in SUBROUTINES: #add a check for subroutine management
                t = Thread(target=self._load_subroutine, args=(subroutine, ))
                t.start()
                threads.append(t)
            #Save changes in config
            if not self.config_dict["status"]:
                self.config_dict["status"] = True
            self._run = True
            self._logger.info(f"Engine for ecosystem {self._ecosystem_name} " +
                              "successfully started")
            if wait_join:
                for t in threads:
                    t.join()
                del threads
        else:
            print(f"Engine {self._ecosystem_name} is already running")

    def stop(self):
        self._logger.info("Stopping engine ...")
        self._stop_scheduler()
        self._run = False
        for subroutine in self._subroutines:
            try:
                subroutine_name = subroutine
                self._subroutines[subroutine].stop()
                self._logger.debug(f"{subroutine_name.capitalize()} " +
                                   "subroutine was stopped")
            except:
                self._logger.error(f"{subroutine_name.capitalize()} " +
                                   "subroutine was not shut down properly")
        self._subroutines = {}
        if self.config_dict["status"]:
            self.config_dict["status"] = False
            #save changes in config
        self._logger.info("Engine stopped")

    def _load_subroutine(self, subroutine):
        try:
            self._logger.debug(f"Starting {subroutine.NAME} subroutine")
            self._subroutines[subroutine.NAME] = subroutine("B612")
            self._logger.debug(f"{subroutine.NAME.capitalize()} subroutine " +
                               "successfully started")
        except:
            self._logger.error(f"{subroutine.NAME.capitalize()} subroutine " +
                               "was not successfully started")

    def _start_scheduler(self):
        h, m = config.HEALTH_LOGGING_TIME.split("h")
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(self._health_routine, trigger="cron",
                               hour=h, minute=m, misfire_grace_time=15*60,
                               id="health")
        self._scheduler.start()

    def _health_routine(self):
        mode = self._subroutines["light"].mode
        status = self._subroutines["light"].status
        self.set_light_on()
        self._subroutines["health"].take_picture()
        if mode == "automatic":
            self.set_light_auto()
        else:
            if status:
                self.set_light_on()
            else:
                self.set_light_off()
        self._subroutines["health"].image_analysis()

    def _stop_scheduler(self):
        self._logger.info("Closing the tasks scheduler")
        try:
            self._scheduler.remove_job("health")
            self._scheduler.shutdown()
            del self._scheduler
            self._logger.info("The tasks scheduler was closed properly")
        except:
            self._logger.error("The tasks scheduler was not closed properly")

    """API calls"""
    #Configuration info
    @property
    def name(self):
        return self._ecosystem_name

    @property
    def uid(self):
        return self._ecosystem_id

    @property
    def config_dict(self):
        return self._config.config_dict

    #Light
    def update_moments(self):
        #In the future: run this only is needed (submodule["light"].method ==
        # "elongate" of "mimic")
        try:
            self._subroutines["light"].update_moments()
        #The subroutine is not currently running
        #In the future: raise exception
        except KeyError:
            pass

    @property
    def light_info(self):
        try:
            return self._subroutines["light"].light_info
        #The subroutine is not currently running, return None object
        except KeyError:
            return None

    def set_light_on(self, countdown=None):
        try:
            self._subroutines["light"].set_light_on()
        #The subroutine is not currently running
        #In the future: raise exception
        except KeyError:
            print("Light subroutine is not running in engine " +
                  f"{self._ecosystem_name}")

    def set_light_off(self, countdown=None):
        try:
            self._subroutines["light"].set_light_off()
        #The subroutine is not currently running
        #In the future: raise exception
        except KeyError:
            print("Light suroutine is not running in engine " +
                  f"{self._ecosystem_name}")

    def set_light_auto(self):
        try:
            self._subroutines["light"].set_light_auto()
        #The subroutine is not currently running, return None object
        except KeyError:
            print("Light suroutine is not running in engine " +
                  f"{self._ecosystem_name}")
    #Sensors
    @property
    def sensors_data(self):
        try:
            return self._subroutines["sensors"].sensors_data
        except KeyError:
            return None
    #Health
    @property
    def plants_health(self):
        try:
            return self._subroutines["health"].get_health_data()
        except KeyError:
            return None

    #Refresh config
    def refresh_config(self):
        for subroutine in self._subroutines:
            self._subroutines[subroutine].refresh_config()

    #Get subroutines running
    @property
    def subroutine_running(self):
        return [subroutine.NAME for subroutine in self._subroutines]


#---------------------------------------------------------------------------
#   Manager classes and functions
#---------------------------------------------------------------------------
class Manager:
    """Create an Engine manager that will coordonate the Engines in case 
    multiple engines are run on a single computer.

    Under normal circumstances only one Engine instance should be created
    for each ecosystem. The manager makes sure this is the case. The 
    manager is automatically instanciated at module load and should be 
    accessed through module functions
    """
    def __init__(self):
        self.engines = {}
        self._engine_started = []
        self._subroutine_dict = {}
        self._logger = logging.getLogger("eng.Manager")
        self._momentsManager = False
        self.autoManager = False

    def start_momentsManager(self):
        self._logger.debug("Starting the moments manager")
        self._scheduler = BackgroundScheduler()
        #No need to use ``_update_moments`` as no engine should have 
        #started
        self._download_moments()
        self._scheduler.add_job(self.refresh_moments, "cron",
                                hour="1", misfire_grace_time=15*60,
                                id="moments")
        self._scheduler.start()
        self.momentsManager = True

    def stop_momentsManager(self):
        self._logger.debug("Shutting the moments manager")
        try:
            self._scheduler.remove_job("moments")
            self._momentsManager = False
            self._scheduler.shutdown()
            del self._scheduler
        except NameError:
            pass

    def _download_moments(self):
        #if at least one need moment and
        if config.is_connected():
            trials = 5
            latitude = globalConfig.home_coordinates["latitude"]
            longitude = globalConfig.home_coordinates["longitude"]
            for count in range(trials):
                try:
                    self._logger.info("Trying to update sunrise and sunset " +
                                      "times on sunrise-sunset.org " +
                                      f"-- trial {count+1}/{trials}")
                    data = requests.get("https://api.sunrise-sunset.org/json?lat="
                                        + str(latitude) + "&lng=" + str(longitude)).json()
                    results = data["results"]
                    with open("engine/cache/sunrise.cch", "w") as outfile:
                        json.dump(results, outfile)
                    self._logger.info("Sunrise and sunset times successfully " +
                                      "updated")
                    return True
                except:
                    if count < trials-1:
                        self._logger.info("Failed to update sunrise and sunset " +
                                          "times, retrying")
                        sleep(0.25)
                    elif count == trials-1:
                        self._logger.error("Failed to update sunrise and " +
                                           "sunset times")
                        return False
        self._logger.error("gaiaEngine is not connected to the Internet, " +
                           "cannot download moments of the day")
        return False

    def _update_moments(self):
        for engine in self.engines:
            self.engines[engine].update_moments()

    def refresh_moments(self):
        if self._download_moments():
            self._update_moments()
        else:
            raise Exception("Cannot update moments, error durring download")

    def _getIds(self, ecosystem):
        if ecosystem in globalConfig.ecosystems_id:
            ecosystem_id = ecosystem
            ecosystem_name = globalConfig.id_to_name_dict[ecosystem]
            return ecosystem_id, ecosystem_name
        elif ecosystem in globalConfig.ecosystems_name:
            ecosystem_id = globalConfig.name_to_id_dict[ecosystem]
            ecosystem_name = ecosystem
            return ecosystem_id, ecosystem_name
        raise ValueError("Please provide a valid ecosystem name or ecosystem id. " +
                         "If you want to create a new ecosystem configuration " +
                         "refer to the module ``config``")

    def createEngine(self, ecosystem, start=False):
        ecosystem_id, ecosystem_name = self._getIds(ecosystem)           
        if ecosystem_id in self._subroutine_dict:
            self.stopSubroutine(ecosystem, "all")
        if ecosystem_id not in self.engines:
            engine = gaiaEngine(ecosystem)
            self.engines[ecosystem_id] = engine
            self._logger.info(f"Engine for ecosystem {ecosystem_name} has " +
                              "been created")
            if start:
                self.startEngine(ecosystem_id)
            return engine
        self._logger.debug(f"Engine for ecosystem {ecosystem_name} already " +
                           "exists")
        return False

    def getEngine(self, ecosystem, start=False):
        ecosystem_id, ecosystem_name = self._getIds(ecosystem)
        if ecosystem_id in self.engines:
            engine = self.engines[ecosystem_id]
        else:
            engine = self.createEngine(ecosystem_id, start=start)
        return engine

    def startEngine(self, ecosystem, wait_join=False):
        ecosystem_id, ecosystem_name = self._getIds(ecosystem)
        if ecosystem_id in self.engines:
            if not self._engine_started:
                config.start_watchdog()
                self.start_momentsManager()
            if ecosystem_id not in self._engine_started:
                engine = self.engines[ecosystem_id]
                self._logger.info("Starting engine for ecosystem " +
                                  f"{ecosystem_name}")
                engine.start(wait_join=wait_join)
                self._engine_started.append(ecosystem_id)
                self._logger.info(f"Engine for ecosystem {ecosystem_name} "+
                                  "started")
                return True
            else:
                self._logger.debug(f"Engine for ecosystem {ecosystem_name} " +
                                   "has already been started")
                return True
        self._logger.warning(f"Engine for ecosystem {ecosystem_name} has " +
                             "not been created yet")
        return False

    def stopEngine(self, ecosystem):
        ecosystem_id, ecosystem_name = self._getIds(ecosystem)
        if ecosystem_id in self.engines:
            if ecosystem_id in self._engine_started:
                engine = self.engines[ecosystem_id]
                engine.stop()
                self._engine_started.remove(ecosystem_id)
                self._logger.info(f"Engine for ecosystem {ecosystem_name} " +
                                  "has been stopped")
                #If no more engines running, stop background routines
                if not self.engines:
                    config.stop_watchdog()
                    self.stop_momentsManager()
                return True
            else:
               self._logger.warning("Cannot stop engine for ecosystem " +
                                    f"{ecosystem_name} as it has not been "+ 
                                    "started yet")
               return False
        else:
            self._logger.warning("Cannot stop engine for ecosystem " +
                                 f"{ecosystem_name} as it does not exist")
            return False

    def delEngine(self, ecosystem):
        ecosystem_id, ecosystem_name = self._getIds(ecosystem)
        if ecosystem_id in self.engines:            
            if ecosystem_id in self._engine_started:
                self._logger.error("Cannot delete a started engine. " +
                                   "First need to stop it")
                return False
            else:
                del self.engines[ecosystem_id]
                self._logger.info(f"Engine for ecosystem {ecosystem_name} " +
                                  "has been deleted")
                return True
        else:
            self._logger.warning("Cannot delete engine for ecosystem " +
                                 f"{ecosystem_name} as it does not exist")
            return False

    def createSubroutine(self, ecosystem, subroutine_name):
        ecosystem_id, ecosystem_name = self._getIds(ecosystem)
        if not subroutine_name in [subroutine.NAME for subroutine in SUBROUTINES]:
            print(f"Subroutine '{subroutine_name}' is not available. Use " +
                  "'subroutines_available()' to see available subroutine names")
            return False
        if ecosystem_id in self.engines:
            self._logger.warning("You cannot create a subroutine for " +
                                 f"{ecosystem_name} if its engine is " +
                                 "already running")
            return False
        if ecosystem_id in self._subroutine_dict:
            module = self._subroutine_dict[ecosystem_id].get(subroutine_name, False)
            if module:
                self._logger.debug(f"{subroutine_name.capitalize()} " +
                                   "subroutine is already running for " +
                                   f"ecosystem {ecosystem_name}")
                return True
            if not module:
                for subroutine in SUBROUTINES:
                    if subroutine.NAME == subroutine_name:
                        self._subroutine_dict[ecosystem_id] = {subroutine_name: 
                                                               subroutine(ecosystem_id)}
                        self._logger.info(f"{subroutine_name.capitalize()} " +
                                          "subroutine created for ecosystem " +
                                          f"{ecosystem_name}")
                        return True
        else:
            for subroutine in SUBROUTINES:
                if subroutine.NAME == subroutine_name:
                    self._subroutine_dict[ecosystem_id] = {subroutine_name: 
                                                           subroutine(ecosystem_id)}
                    self._logger.debug(f"{subroutine_name.capitalize()}  " +
                                       "subroutine created for ecosystem " +
                                       f"{ecosystem_name}")
                    return True

    def stopSubroutine(self, ecosystem, subroutine_name):
        ecosystem_id, ecosystem_name = self._getIds(ecosystem)
        if not subroutine_name in [subroutine.NAME for subroutine in SUBROUTINES]:
            print(f"Subroutine '{subroutine_name}' is not available. Use " +
                  "'subroutines_available()' to see available subroutine")
            return False
        if subroutine_name == "all":
            for subroutine in self._subroutine_dict[ecosystem_id]:
                self._subroutine_dict[ecosystem_id][subroutine].stop()
            self._logger.info("All subroutines have been stopped for ecosystem" +
                              "{ecosystem_name}")
            return True
        try:
            self._self._subroutine_dict[ecosystem_id]["subroutine_name"].stop()
            self._logger.info(f"{subroutine_name.capitalize()} subroutine " +
                              f"has been stopped for ecosystem {ecosystem_name}")
            return True
        except:
            self._logger.warning(f"Cannot stop {subroutine_name} subroutine for " +
                                 f"ecosystem {ecosystem_name} as it has does not " +
                                 " exist")
            return False

    def _autoManage(self):
        config.start_watchdog()
        while self.autoManager:
            new_config.wait()
            #this happens when stopping autoManager
            if not self.autoManager:
                break
            expected_started = []           
            to_delete = list(self.engines.keys())
            for ecosystem in globalConfig.ecosystems_id:
                #create engine if it doesn't exist
                if ecosystem not in self.engines:
                    self.createEngine(ecosystem)
                #remove the ecosystem from the to_delete_list
                else:
                    to_delete.remove(ecosystem)
                #check if the engine is expected to be running
                if globalConfig.status(ecosystem) == True:
                    expected_started.append(ecosystem)
            
            #start engines which are expected to run and are not running
            for ecosystem in expected_started:
                if ecosystem not in self._engine_started:
                    self.startEngine(ecosystem, wait_join=True)
            #start engines which are not expected to run and are currently 
            #running
            for ecosystem in self._engine_started:
                if ecosystem not in expected_started:
                    self.stopEngine(ecosystem)
            #delete engines which were created and are no longer on the 
            #config file
            for ecosystem in to_delete:
                self.delEngine(ecosystem)
            if CLIENT:
                sio.emit("engines_change")
            new_config.clear()
        if self.clear_manager:
            for ecosystem in self._engine_started:
                self.stopEngine(ecosystem)
            to_delete = list(self.engines.keys())
            for ecosystem in to_delete:
                self.stopEngine(ecosystem)
                self.delEngine(ecosystem)
            
    def start_autoManage(self):
        self._logger.info("Starting the autoManager ...")
        self._logger = logging.getLogger("eng.autoManager")
        self._logger.info("autoManager started")
        self.autoManager = True
        self.clear_manager = False
        self.autoManage = Thread(target=self._autoManage)
        self.autoManage.start()
        #send a new config signal so a first loop starts
        new_config.set()
        
    def stop_autoManage(self, clear_manager=True):
        self._logger.info("Stoping the autoManager ...")
        self.autoManager = False
        self.clear_manager = clear_manager
        #send a new config signal so a last loops starts
        new_config.set()
        try:
            self.autoManage.join()
            del self.autoManage
        finally:
            self._logger = logging.getLogger("eng.Manager")
            self._logger.info("autoManager stopped")


_manager = Manager()


#---------------------------------------------------------------------------
#   Functions to interact with the module
#---------------------------------------------------------------------------
def createEngine(ecosystem, start=False):
    """Create an engine for the specified ecosystem. 
    
    :param ecosystem: The ecosystem id or name, as defined in the 
                      ``ecosystems.cfg`` file.
    :param start: If ``False``, the Engine will not start after beeing 
                  instanciated. If ``True``, the engine will start its
                  subroutines after instanciation. Default to ``False``
    
    Return an Engine object if the Engine and all its subroutines was
    correctly created, ``False`` otherwise or if the Engine already 
    existed for the given ecosystem.
    
    Rem: cannot be used if the autoManager has been started.
    """
    if not _manager.autoManager:
        return _manager.createEngine(ecosystem, start=start)
    raise Exception("You cannot manually manage engines while the " +
                    "autoManager is running")

def getEngine(ecosystem, start=False):
    """Returns the engine for the specified ecosystem. 
    
    :param ecosystem: The ecosystem id or name, as defined in the 
                      ``ecosystems.cfg`` file.
    :param start: If ``False``, the Engine will not start after beeing 
                  instanciated. If ``True``, the engine will start its
                  subroutines after instanciation. Default to ``False``

    Return the required Engine object if if exists. If it does not 
    exist, the required Engine will be created and returned.
    
    Rem: cannot be used if the autoManager has been started.
    """
    if not _manager.autoManager:
        return _manager.getEngine(ecosystem, start=start)
    raise Exception("You cannot manually manage engines while the " +
                    "autoManager is running")

def startEngine(ecosystem, wait_join=False):
    """Start the engine for the specified ecosystem. 
    
    :param ecosystem: The ecosystem id or name, as defined in the 
                      ``ecosystems.cfg`` file.
    :param wait_join: If ``False``, the Engine will not wait for the
                      initialization of all its subroutines. If 
                      ``True``, the engine will will not wait for the
                      initialization of all its subroutines.
                      Default to ``False``

    Return ``True`` is the engine as been properly started or is already
    running, ``False`` if the Engine has not been created yet.
    
    Rem: cannot be used if the autoManager has been started.
    """
    if not _manager.autoManager:
        return _manager.startEngine(ecosystem, wait_join=wait_join)
    raise Exception("You cannot manually manage engines while the " +
                    "autoManager is running")

def stopEngine(ecosystem):
    """Stop the engine for the specified ecosystem. 
    
    :param ecosystem: The ecosystem id or name, as defined in the 
                      ``ecosystems.cfg`` file.
    
    Return ``True`` if the engine and all its subroutines stopped 
    correctly, ``False`` otherwise.
    """
    if not _manager.autoManager:
        return _manager.stopEngine(ecosystem)
    raise Exception("You cannot manually manage engines while the autoManager is running")    

def delEngine(ecosystem):   
    """Delete the engine for the specified ecosystem from the Manager 
    internal dict.
    
    :param ecosystem: The ecosystem id or name, as defined in the 
                      ``ecosystems.cfg`` file.
    
    Return ``True`` if the engine is deleted, ``False`` otherwise.
    """
    if not _manager.autoManager:
        return _manager.delEngine(ecosystem)
    raise Exception("You cannot manually manage engines while the autoManager is running")

def getEngineDict():
    """Return the internal dict from the Manager"""
    return _manager.engines

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
    if not _manager.autoManager:
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
    if not _manager.autoManager:
        return _manager.stopSubroutine(ecosystem, subroutine)
    raise Exception("You cannot manually manage subroutines while the autoManager is running")

def subroutines_available():
    """
    Returns a list with all the subroutines available
    """
    return [subroutine.NAME for subroutine in SUBROUTINES]

class autoManager:
    """Abstraction layer to interact with the Engines automatic manager.

    The autoManager will automatically start and stop Engines based on the 
    configuration files.
    """
    @staticmethod
    def start():
        """Start the Engines automatic manager.
        """
        _manager.start_autoManage()
    @staticmethod
    def stop(clear_manager=True):
        """Stop the Engines automatic manager.
        """
        _manager.stop_autoManage(clear_manager=clear_manager)
    @staticmethod
    def status(self):
        """Return the current status of the autoManager
        """
        return _manager.autoManager