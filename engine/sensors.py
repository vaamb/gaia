from datetime import datetime
from threading import Event, Thread, Lock
from time import sleep, monotonic

from config import Config
from engine.config_parser import localTZ
from engine.hardware_library import SENSORS_AVAILABLE
from engine.subroutine_template import subroutineTemplate

lock = Lock()
# TODO: add an event here so can call a dispatcher to say when new data (for climate amongst others)


class gaiaSensors(subroutineTemplate):
    NAME = "sensors"

    def __init__(self, ecosystem=None, engine=None) -> None:
        super().__init__(ecosystem=ecosystem, engine=engine)

        self._timezone = localTZ
        self._sensors = []
        self._data = {}

        self._finish__init__()

    def _add_sensor(self, hardware_uid: str) -> None:
        model = self._config.IO_dict[hardware_uid]["model"]
        try:
            sensor = SENSORS_AVAILABLE[model]
            name = self._config.IO_dict[hardware_uid]["name"]
            s = sensor(
                uid=hardware_uid,
                name=name,
                address=self._config.IO_dict[hardware_uid]["address"],
                model=self._config.IO_dict[hardware_uid]["model"],
                # type is automatically provided as it is a
                level=self._config.IO_dict[hardware_uid]["level"],
                measure=self._config.IO_dict[hardware_uid]["measure"]
                       if "measure" in self._config.IO_dict[hardware_uid]
                       else None,
                plant=self._config.IO_dict[hardware_uid]["plant"]
                      if "plant" in self._config.IO_dict[hardware_uid]
                      else None,
            )
            self._sensors.append(s)
            self._logger.debug(f"Sensor {name} has been set up")

        except KeyError:
            self._logger.error(f"{model} is not in the list of "
                               "the supported sensors")

    def _remove_sensor(self, hardware_uid: str) -> None:
        try:
            index = [h.uid for h in self._sensors].index(hardware_uid)
        except ValueError:
            self._logger.error(f"Sensor '{hardware_uid}' does not exist")
        del self._sensors[index]

    def _start_sensors_loop(self) -> None:
        self._logger.debug(f"Starting sensors")
        self.refresh_hardware()
        self._stopEvent = Event()
        self._sensorsLoopThread = Thread(target=self._sensors_loop, args=())
        self._sensorsLoopThread.name = f"sensorsLoop-{self._config.ecosystem_id}"
        self._sensorsLoopThread.start()
        self._logger.debug(f"Sensors loop successfully started")

    def _stop_sensors_loop(self) -> None:
        self._logger.debug(f"Stopping sensors loop")
        self._stopEvent.set()
        self._sensorsLoopThread.join()
        del self._sensorsLoopThread, self._stopEvent

    def _sensors_loop(self) -> None:
        while not self._stopEvent.is_set():
            start_time = monotonic()
            self._logger.debug("Starting sensors data update routine ...")
            self._update_sensors_data()
            loop_time = monotonic() - start_time
            sleep_time = Config.SENSORS_TIMEOUT - loop_time
            # TODO: add event for climate
            if sleep_time < 0:
                self._logger.warning(f"Sensors data loop took {loop_time}. This "
                                     f"either indicates an error occurred or the "
                                     f"need to adapt Config.SENSOR_TIMEOUT")
                sleep_time = 2
            self._logger.debug(
                f"Sensors data update finished in {loop_time:.1f}" +
                f"s. Next sensors data update in {sleep_time:.1f}s")
            self._stopEvent.wait(sleep_time)

    def _update_sensors_data(self) -> None:
        """
        Loops through all the sensors and stores the value in self._data
        """
        cache = {}
        now = datetime.now().replace(microsecond=0)
        now_tz = now.astimezone(self._timezone)
        cache["datetime"] = now_tz
        cache["data"] = {}
        for sensor in self._sensors:
            cache["data"].update({sensor.uid: sensor.get_data()})
            sleep(0.01)
        with lock:
            self._data = cache

    def _start(self):
        self._start_sensors_loop()

    def _stop(self):
        self._stop_sensors_loop()
        self._data = {}

    """API calls"""
    def refresh_hardware(self) -> None:
        # TODO: rebuild so it adds sensors if needed and delete them if not needed anymore
        for hardware_uid in self._config.get_sensors():
            self._add_sensor(hardware_uid)

    @property
    def sensors_data(self) -> dict:
        """
        Get sensors data as a dict with the following format:
        {
        "datetime": datetime.now(),
        "data":
            "sensor_uid1": sensor_data1

        }
        """
        return self._data
