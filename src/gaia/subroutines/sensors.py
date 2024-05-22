from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from threading import Lock
from time import monotonic
import typing as t
from typing import Literal

from apscheduler.triggers.interval import IntervalTrigger

import gaia_validators as gv

from gaia.hardware import sensor_models
from gaia.hardware.abc import BaseSensor
from gaia.subroutines.template import SubroutineTemplate


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.subroutines.climate import Climate
    from gaia.subroutines.light import Light


class Sensors(SubroutineTemplate):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hardware_choices = sensor_models
        self.hardware: dict[str, BaseSensor]
        self._loop_timeout: float = float(
            self.ecosystem.engine.config.app_config.SENSORS_LOOP_PERIOD)
        self._sensors_data: gv.SensorsData | gv.Empty = gv.Empty()
        self._data_lock = Lock()
        self._finish__init__()

    def _sensors_routine(self) -> None:
        start_time = monotonic()
        self.logger.debug("Starting sensors data update routine ...")
        try:
            self.update_sensors_data()
        except Exception as e:
            self.logger.error(
                f"Encountered an error while updating sensors data. "
                f"ERROR msg: `{e.__class__.__name__} :{e}`."
            )
        try:
            self.send_data()
        except Exception as e:
            self.logger.error(
                f"Encountered an error while sending sensors data and warnings. "
                f"ERROR msg: `{e.__class__.__name__} :{e}`."
            )
        loop_time = monotonic() - start_time
        if loop_time > self._loop_timeout:  # pragma: no cover
            self.logger.warning(
                f"Sensors data loop took {loop_time:.1f}. This either "
                f"indicates errors while data retrieval or the need to "
                f"adapt 'SENSOR_LOOP_PERIOD'."
            )
        self.logger.debug(
            f"Sensors data update finished in {loop_time:.1f} s."
        )

    def _compute_if_manageable(self) -> bool:
        if self.config.get_IO_group_uids(gv.HardwareType.sensor):
            return True
        else:
            self.logger.warning("No sensor detected.")
            return False

    def _start(self) -> None:
        self.logger.info(
            f"Starting the sensors loop. It will run every "
            f"{self._loop_timeout:.1f} s.")
        self.ecosystem.engine.scheduler.add_job(
            func=self._sensors_routine,
            id=f"{self.ecosystem.uid}-sensors_routine",
            trigger=IntervalTrigger(seconds=self._loop_timeout, jitter=self._loop_timeout/10),
        )
        self.logger.debug(f"Sensors loop successfully started")

    def _stop(self) -> None:
        self.logger.info(f"Stopping sensors loop")
        if self.ecosystem.get_subroutine_status("climate"):
            self.ecosystem.stop_subroutine("climate")
        self.ecosystem.engine.scheduler.remove_job(
            f"{self.ecosystem.uid}-sensors_routine")
        self.hardware = {}

    """API calls"""
    def add_hardware(self, hardware_config: gv.HardwareConfig) -> BaseSensor:
        model = hardware_config.model
        if self.ecosystem.engine.config.app_config.VIRTUALIZATION:
            if not model.startswith("virtual"):
                hardware_config.model = f"virtual{model}"
        hardware = super().add_hardware(hardware_config)
        if self.ecosystem.get_subroutine_status("light"):
            light_subroutine: Light = self.ecosystem.subroutines["light"]
            light_subroutine.reset_light_sensors()
        return hardware

    def remove_hardware(self, hardware_uid: str) -> None:
        super().remove_hardware(hardware_uid)
        if self.ecosystem.get_subroutine_status("light"):
            light_subroutine: Light = self.ecosystem.subroutines["light"]
            light_subroutine.reset_light_sensors()

    def get_hardware_needed_uid(self) -> set[str]:
        return set(self.config.get_IO_group_uids(gv.HardwareType.sensor))

    def refresh_hardware(self) -> None:
        super().refresh_hardware()
        if self.ecosystem.get_subroutine_status("climate"):
            climate_subroutine: "Climate" = self.ecosystem.subroutines["climate"]
            climate_subroutine.refresh_hardware()

    @property
    def sensors_data(self) -> gv.SensorsData | gv.Empty:
        with self._data_lock:
            return self._sensors_data

    @sensors_data.setter
    def sensors_data(self, data: gv.SensorsData | gv.Empty) -> None:
        with self._data_lock:
            self._sensors_data = data

    def _add_sensor_records(self, cache: gv.SensorsDataDict) -> gv.SensorsDataDict:
        futures = [
            self.executor.submit(hardware.get_data)
            for hardware in self.hardware.values()
        ]
        sensors_data = [future.result() for future in futures]
        for sensor in sensors_data:
            cache["records"].extend(
                sensor_record for sensor_record in sensor
                if sensor_record.value is not None
            )
        return cache

    def _add_sensor_averages(self, cache: gv.SensorsDataDict) -> gv.SensorsDataDict:
        to_average: dict[str, list[float]] = {}
        for record in cache["records"]:
            try:
                to_average[record.measure].append(record.value)
            except KeyError:
                to_average[record.measure] = [record.value]
        average = [
            gv.MeasureAverage(
                measure=measure,
                value=round(mean(value), 2),
                timestamp=None
            )
            for measure, value in to_average.items()
        ]
        cache["average"] = average
        return cache

    def _add_sensor_warnings(self, cache: gv.SensorsDataDict) -> gv.SensorsDataDict:
        # Get the target, the hysteresis and the alarm threshold
        pod: Literal["day", "night"] = self.config.period_of_day.name
        parameter_limits: dict[str, tuple[float, float, float]] = {
            parameter: (values[pod], values["hysteresis"], values["alarm"])
            for parameter, values in self.config.climate.items()
            if values["alarm"]
        }
        # If no `parameter_limits`: stop
        if not parameter_limits:
            return cache
        sensor_warnings: list[gv.SensorAlarm] = []
        for record in cache["records"]:
            if not record.measure in parameter_limits:
                continue
            p_lim = parameter_limits[record.measure]
            direction: gv.Position
            delta: float
            if record.value < p_lim[0] - p_lim[1]:
                direction = gv.Position.under
                delta = p_lim[0] - p_lim[1] - record.value
            elif record.value > p_lim[0] + p_lim[1]:
                direction = gv.Position.above
                delta = p_lim[0] - p_lim[1] - record.value
            else:
                continue
            level: gv.WarningLevel
            if p_lim[2] < delta <= 1.5 * p_lim[2]:
                level = gv.WarningLevel.moderate
            elif 1.5 * p_lim[2] < delta <= 2.0 * p_lim[2]:
                level = gv.WarningLevel.high
            else:
                level = gv.WarningLevel.critical
            sensor_warnings.append(gv.SensorAlarm(
                sensor_uid=record.sensor_uid,
                measure=record.measure,
                position=direction,
                delta=delta,
                level=level,
            ))
        cache["alarms"] = sensor_warnings
        return cache

    def update_sensors_data(self) -> None:
        """
        Loops through all the sensors and stores the value in self._data
        """
        if not self.started:
            raise RuntimeError(
                "Sensors subroutine has to be started to update the sensors data"
            )
        cache: gv.SensorsDataDict = {
            "timestamp": datetime.now(timezone.utc).replace(microsecond=0),
            "records": [],
            "average": [],
            "alarms": [],
        }
        cache = self._add_sensor_records(cache)
        cache = self._add_sensor_averages(cache)
        alarms_flag = gv.ManagementFlags.alarms
        if (self.config.management_flag & alarms_flag == alarms_flag):
            cache = self._add_sensor_warnings(cache)
        if len(cache["records"]) > 0:
            self.sensors_data = gv.SensorsData(**cache)
        else:
            self.sensors_data = gv.Empty()

    def send_data(self) -> None:
        if not self.ecosystem.engine.use_message_broker:
            return
        self.ecosystem.engine.event_handler.send_payload_if_connected(
            "sensors_data", ecosystem_uids=[self.ecosystem.uid])
