from __future__ import annotations

from datetime import datetime
from statistics import mean
from threading import Event, Thread, Lock
from time import monotonic
import typing as t
from typing import Any

from gaia_validators import Empty, HardwareConfigDict, SensorsData

from gaia.config import get_config
from gaia.hardware import sensor_models
from gaia.hardware.abc import BaseSensor
from gaia.subroutines.template import SubroutineTemplate


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.subroutines.climate import Climate


lock = Lock()


class Sensors(SubroutineTemplate):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._stop_event = Event()
        self._data: SensorsData | Empty = Empty()
        self._finish__init__()

    def _sensors_loop(self) -> None:
        while not self._stop_event.is_set():
            start_time = monotonic()
            self.logger.debug("Starting sensors data update routine ...")
            self.update_sensors_data()
            loop_time = monotonic() - start_time
            sleep_time = get_config().SENSORS_TIMEOUT - loop_time
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
        if self.config.get_IO_group_uids("sensor"):
            self.manageable = True
        else:
            self.logger.warning(
                "No sensor detected, disabling Sensors subroutine"
            )
            self.manageable = False

    def _start(self):
        self.refresh_hardware()
        self.logger.info(
            f"Starting sensors loop. It will run every {get_config().SENSORS_TIMEOUT} s"
        )
        self._thread = Thread(target=self._sensors_loop, args=())
        self._thread.name = f"{self._uid}-sensors_loop"
        self._thread.start()
        self.logger.debug(f"Sensors loop successfully started")

    def _stop(self):
        self.logger.info(f"Stopping sensors loop")
        self._stop_event.set()
        self._thread.join()
        if self.ecosystem.get_subroutine_status("climate"):
            climate_subroutine: "Climate" = self.ecosystem.subroutines["climate"]
            climate_subroutine.stop()
        self.hardware = {}

    """API calls"""
    def add_hardware(self, hardware_dict: HardwareConfigDict) -> BaseSensor:
        model = hardware_dict.get("model", None)
        if get_config().VIRTUALIZATION:
            if not model.startswith("virtual"):
                hardware_dict["model"] = f"virtual{model}"
        return self._add_hardware(hardware_dict, sensor_models)

    def get_hardware_needed_uid(self) -> set[str]:
        return set(self.config.get_IO_group_uids("sensor"))

    def refresh_hardware(self) -> None:
        super().refresh_hardware()
        if self.ecosystem.get_subroutine_status("climate"):
            climate_subroutine: "Climate" = self.ecosystem.subroutines["climate"]
            climate_subroutine.refresh_hardware()

    def update_sensors_data(self) -> None:
        """
        Loops through all the sensors and stores the value in self._data
        """
        cache: dict[str, Any] = {}
        to_average: dict[str, list] = {}
        now = datetime.now().astimezone().replace(microsecond=0)
        cache["timestamp"]: datetime = now
        cache["records"]: list[dict] = []
        for uid in self.hardware:
            measures = self.hardware[uid].get_data()
            cache["records"].append(
                {"sensor_uid": uid, "measures": measures}
            )
            for measure in measures:
                try:
                    to_average[measure["measure"]].append(measure["value"])
                except KeyError:
                    to_average[measure["measure"]] = [measure["value"]]
        average: dict[str, float | int] = {}
        for measure in to_average:
            average[measure] = round(mean(to_average[measure]), 2)
        cache["average"] = [
            {"measure": measure, "value": value} for measure, value in average.items()
        ]
        with lock:
            self._data = SensorsData(**cache)

    @property
    def sensors_data(self) -> SensorsData:
        """
        Get sensors data as a dict with the following format:
        {
            "timestamp": datetime.now(),
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
