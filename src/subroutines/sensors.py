from datetime import datetime
from statistics import mean
from threading import Event, Thread, Lock
from time import monotonic

from ..exceptions import HardwareNotFound
from ..hardware.ABC import BaseSensor
from ..hardware.store import SENSORS
from ..subroutines.template import SubroutineTemplate
from config import Config


lock = Lock()


class Sensors(SubroutineTemplate):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._stop_event = Event()
        self._data = {}
        self._finish__init__()

    def _sensors_loop(self) -> None:
        while not self._stop_event.is_set():
            start_time = monotonic()
            self.logger.debug("Starting sensors data update routine ...")
            self.update_sensors_data()
            loop_time = monotonic() - start_time
            sleep_time = Config.SENSORS_TIMEOUT - loop_time
            if sleep_time < 0:  # pragma: no cover
                self.logger.warning(
                    f"Sensors data loop took {loop_time}. This either indicates "
                    f"an error occurred or the need to adapt SENSOR_TIMEOUT"
                )
                sleep_time = 2
            self.logger.debug(
                f"Sensors data update finished in {loop_time:.1f}" +
                f"s. Next sensors data update in {sleep_time:.1f}s"
            )
            self._stop_event.wait(sleep_time)

    def _update_manageable(self) -> None:
        if self.config.get_IO_group("sensor"):
            self.manageable = True
        else:
            self.logger.warning(
                "No sensor detected, disabling Sensors subroutine"
            )
            self.manageable = False

    def _start(self):
        self.refresh_hardware()
        self.logger.info(
            f"Starting sensors loop. It will run every {Config.SENSORS_TIMEOUT} s"
        )
        self._thread = Thread(target=self._sensors_loop, args=())
        self._thread.name = f"{self._uid}-sensors_loop"
        self._thread.start()
        self.logger.debug(f"Sensors loop successfully started")

    def _stop(self):
        self.logger.info(f"Stopping sensors loop")
        self._stop_event.set()
        self._thread.join()
        if self.ecosystem.subroutines["climate"].status:
            self.ecosystem.subroutines["climate"].stop()
        self.hardware = {}

    """API calls"""
    def add_hardware(self, hardware_dict: dict) -> BaseSensor:
        hardware_uid = list(hardware_dict.keys())[0]
        try:
            model = hardware_dict[hardware_uid].get("model", None)
            if Config.VIRTUALIZATION:
                if not model.startswith("virtual"):
                    hardware_dict[hardware_uid]["model"] = f"virtual{model}"
            hardware = self._add_hardware(hardware_dict, SENSORS)
            self.hardware[hardware_uid] = hardware
            self.logger.debug(f"Sensor {hardware.name} has been set up")
            return hardware
        except HardwareNotFound as e:
            self.logger.error(f"{e.__class__.__name__}: {e}")
        except KeyError as e:
            self.logger.error(
                f"Could not configure sensor {hardware_uid}, one of the "
                f"required info is missing. ERROR msg: {e}"
            )

    def remove_hardware(self, sensors_uid: str) -> None:
        try:
            del self.hardware[sensors_uid]
        except KeyError:
            self.logger.error(f"Sensor '{sensors_uid}' does not exist")

    def refresh_hardware(self) -> None:
        self._refresh_hardware("sensor")
        if (
                self.config.get_management("climate") and
                self.ecosystem.subroutines.get("climate", False)
        ):
            try:
                self.ecosystem.subroutines["climate"].refresh_hardware()
            except Exception as e:
                self.logger.error(
                    f"Could not update climate routine hardware. Error msg: {e}"
                )

    def update_sensors_data(self) -> None:
        """
        Loops through all the sensors and stores the value in self._data
        """
        cache = {}
        average = {}
        now = datetime.now().replace(microsecond=0)
        cache["datetime"] = now
        cache["data"] = []
        for uid in self.hardware:
            measures = self.hardware[uid].get_data()
            cache["data"].append(
                {"sensor_uid": uid, "measures": measures}
            )
            for measure in measures:
                try:
                    average[measure["name"]].append(measure["value"])
                except KeyError:
                    average[measure["name"]] = [measure["value"]]
        for measure in average:
            average[measure] = round(mean(average[measure]), 2)
        cache["average"] = [
            {"name": name, "value": value} for name, value in average.items()
        ]
        with lock:
            self._data = cache

    @property
    def sensors_data(self) -> dict:
        """
        Get sensors data as a dict with the following format:
        {
            "datetime": datetime.now(),
            "data": [
                {
                    "sensor_uid": sensor1_uid,
                    "measures": [
                        {"name": measure1, "value": sensor1_measure1_value},
                    ],
                },
            ],
            "average": [
                {"name": measure, "value": average_measure_value}
            }],
        }
        """
        return self._data
