from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from threading import Event, Thread, Lock
from time import monotonic
import typing as t
from typing import cast

from gaia_validators import (
    Empty, HardwareConfig, MeasureAverage, SensorsData, SensorsDataDict,
    SensorRecord)

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
        self.hardware: dict[str, BaseSensor]
        self._thread: Thread | None = None
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

    def _start(self) -> None:
        time_out: float = get_config().SENSORS_TIMEOUT
        self.logger.info(
            f"Starting sensors loop. It will run every {time_out} s")
        self._stop_event.clear()
        self.thread = Thread(target=self._sensors_loop, args=())
        self.thread.name = f"{self._uid}-sensors_loop"
        self.thread.start()
        self.logger.debug(f"Sensors loop successfully started")

    def _stop(self) -> None:
        self.logger.info(f"Stopping sensors loop")
        self._stop_event.set()
        self.thread.join()
        self.thread = None
        if self.ecosystem.get_subroutine_status("climate"):
            climate_subroutine = cast("Climate", self.ecosystem.subroutines["climate"])
            climate_subroutine.stop()
        self.hardware = {}

    """API calls"""
    @property
    def thread(self) -> Thread:
        if self._thread is None:
            raise ValueError("Thread has not been set up")
        else:
            return self._thread

    @thread.setter
    def thread(self, thread: Thread | None) -> None:
        self._thread = thread

    def add_hardware(self, hardware_config: HardwareConfig) -> BaseSensor:
        model = hardware_config.model
        if get_config().VIRTUALIZATION:
            if not model.startswith("virtual"):
                hardware_config.model = f"virtual{model}"
        return self._add_hardware(hardware_config, sensor_models)

    def get_hardware_needed_uid(self) -> set[str]:
        return set(self.config.get_IO_group_uids("sensor"))

    def refresh_hardware(self) -> None:
        super().refresh_hardware()
        if self.ecosystem.get_subroutine_status("climate"):
            climate_subroutine = cast("Climate", self.ecosystem.subroutines["climate"])
            climate_subroutine.refresh_hardware()

    def update_sensors_data(self) -> None:
        """
        Loops through all the sensors and stores the value in self._data
        """
        cache: SensorsDataDict = {}
        to_average: dict[str, list[float]] = {}
        cache["timestamp"] = datetime.now(timezone.utc).replace(microsecond=0)
        cache["records"] = []
        for uid in self.hardware:
            cache["records"].extend(
                data for data in self.hardware[uid].get_data()
                if data.value is not None
            )
        for record in cache["records"]:
            try:
                to_average[record.measure].append(record.value)
            except KeyError:
                to_average[record.measure] = [record.value]
        cache["average"] = [
            MeasureAverage(
                measure=measure,
                value=round(mean(value), 2),
                timestamp=None
            )
            for measure, value in to_average.items()
        ]
        with lock:
            if len(cache["records"]) > 0:
                self._data = SensorsData(**cache)
            else:
                self._data = Empty()

    @property
    def sensors_data(self) -> SensorsData | Empty:
        return self._data
