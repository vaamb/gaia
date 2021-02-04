from datetime import datetime
import logging
from threading import Event, Thread, Lock
from time import sleep, time

from config import Config
from engine.config_parser import configWatchdog, getConfig, localTZ
from .hardware_library import SENSORS_AVAILABLE

lock = Lock()


class gaiaSensors:
    NAME = "sensors"

    def __init__(self, ecosystem):
        configWatchdog.start()
        self._config = getConfig(ecosystem)
        self._ecosystem = self._config.name
        self._logger = logging.getLogger(f"eng.{self._ecosystem}.Sensors")
        self._logger.debug(f"Initializing gaiaSensors for {self._ecosystem}")
        self._timezone = localTZ
        self._started = False
        self._start_sensors_loop()
        self._logger.debug(
            f"gaiaSensors has been initialized for {self._ecosystem}")

    def _setup_sensors(self):
        self._sensors = []
        for hardware_uid in self._config.get_sensors():
            self._add_sensor(hardware_uid)

    def _add_sensor(self, hardware_uid: str) -> None:
        try:
            model = self._config.IO_dict[hardware_uid]["model"]
            sensor = SENSORS_AVAILABLE[model]
        except KeyError:
            self._logger.error(f"{model} is not in the list of "
                               f"sensors available")
            pass
        name = self._config.IO_dict[hardware_uid]["name"]
        s = sensor(
            hardware_uid=hardware_uid,
            address=self._config.IO_dict[hardware_uid]["address"],
            model=self._config.IO_dict[hardware_uid]["model"],
            name=name,
            level=self._config.IO_dict[hardware_uid]["level"],
            measure=self._config.IO_dict[hardware_uid]["measure"]
                if "measure" in self._config.IO_dict[hardware_uid]
                else None
        )
        self._sensors.append(s)
        self._logger.debug(f"Sensor {name} has been set up")

    def _remove_sensor(self, hardware_uid: str) -> None:
        try:
            index = [h.uid for h in self._sensors].index(hardware_uid)
        except ValueError:
            self._logger.error(f"Sensor '{hardware_uid}' does not exist")
        del self._sensors[index]

    def _start_sensors_loop(self) -> None:
        self._logger.debug(f"Starting sensors loop for {self._ecosystem}")
        self.refresh_hardware()
        self._stopEvent = Event()
        self._data = {}
        self._sensorsLoopThread = Thread(target=self._sensors_loop, args=())
        self._sensorsLoopThread.name = f"sensorsLoop-{self._config.ecosystem_id}"
        self._sensorsLoopThread.start()
        self._logger.debug(f"Sensors loop started for {self._ecosystem}")
        self._started = True

    def _stop_sensors_loop(self) -> None:
        self._logger.debug(f"Stopping sensors loop for {self._ecosystem}")
        self._stopEvent.set()
        self._sensorsLoopThread.join()
        del self._sensorsLoopThread, self._stopEvent
        self._started = False

    def _sensors_loop(self) -> None:
        while not self._stopEvent.is_set():
            start_time = time()
            self._logger.debug("Starting the data update routine")
            self._update_sensors_data()
            loop_time = time() - start_time
            sleep_time = Config.SENSORS_TIMEOUT - loop_time
            if sleep_time < 0:
                sleep_time = 2
            self._logger.debug(
                f"Sensors data update finished in {loop_time:.1f}" +
                f"s. Next data update in {sleep_time:.1f}s")
            self._stopEvent.wait(sleep_time)

    def _update_sensors_data(self) -> None:
        """
        Loops through all the sensors and stores the value in self._data
        """
        self._cache = {}
        now = datetime.now().replace(microsecond=0)
        now_tz = now.astimezone(self._timezone)
        self._cache["datetime"] = now_tz
        self._cache["data"] = {}
        for sensor in self._sensors:
            self._cache["data"].update({sensor.uid: sensor.get_data()})
            sleep(0.01)
        with lock:
            self._data = self._cache
        del self._cache

    """API calls"""

    # configuration info
    def refresh_hardware(self) -> None:
        self._setup_sensors()

    # data
    @property
    def sensors_data(self) -> dict:
        return self._data

    def stop(self) -> None:
        self._logger.debug(f"Stopping gaiaSensors for {self._ecosystem}")
        self._stop_sensors_loop()
        self._logger.debug(
            f"gaiaSensors has been stopped for {self._ecosystem}")
