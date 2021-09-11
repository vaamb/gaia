import hashlib
import json
import logging
import logging.config
from threading import Thread
import weakref

from src.shared_resources import thread_pool
from src.config_parser import get_config
from src.subroutines import SUBROUTINES


class Engine:
    """Create an Engine for a given ecosystem.

    The Engine is an object that manages all the required subroutines.
    IO intensive subroutines are launched in separate threads.

    User should use the module functions to interact with Engines
    rather than instantiate this class
    """

    def __init__(self, ecosystem_id, manager):
        self._config = get_config(ecosystem_id)
        self._ecosystem_uid = self._config.ecosystem_id
        self._ecosystem_name = self._config.name
        self._manager = weakref.proxy(manager)
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

    def __eq__(self, other):
        h = hashlib.sha256()
        h.update(json.dumps(self.config, sort_keys=True).encode())
        h.update(json.dumps(self.subroutines_started, sort_keys=True).encode())
        return h.digest() == other

    @property
    def _socketIO_client(self):
        return self._manager.socketIO_client

    @property
    def _socketIO_enabled(self):
        return self._manager.socketIO_enabled

    def start(self):
        if not self._started:
            self.logger.info("Starting Engine")
            # Start subroutines in thread as they are IO bound. After
            # subroutines initialization is finished, all threads are deleted 
            # and IO-bound subroutines tasks are handled in their own thread.
            for subroutine in self._config.get_managed_subroutines():
                thread_pool.submit(self._start_subroutine, subroutine=subroutine)
            self.logger.debug(f"Engine successfully started")
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
        return self._config.config_dict

    # Light
    def update_sun_times(self):
        self.subroutines["light"].update_sun_times()

    @property
    def light_info(self):
        return self.subroutines["light"].light_info

    def turn_light(self, mode="automatic", countdown=0.0):
        try:
            self.subroutines["light"].turn_light(mode=mode, countdown=countdown)
        # The subroutine is not currently running
        except RuntimeError as e:
            self.logger.error(e)

    def turn_actuator(self,
                      actuator: str,
                      mode: str = "automatic",
                      countdown: float = 0.0) -> None:
        try:
            if actuator.lower() == "light":
                self.subroutines["light"].turn_light(mode=mode,
                                                     countdown=countdown)
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
