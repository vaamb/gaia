from __future__ import annotations

import asyncio
from asyncio import Task
from datetime import datetime, timezone
from math import floor
from statistics import mean
from time import monotonic
import typing as t
from typing import cast, Literal

from apscheduler.triggers.interval import IntervalTrigger

import gaia_validators as gv

from gaia.hardware import sensor_models
from gaia.hardware.abc import BaseSensor
from gaia.subroutines.template import SubroutineTemplate


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.subroutines.climate import Climate
    from gaia.subroutines.light import Light


class _SensorFuture(Task):
    hardware_uid: str


class Sensors(SubroutineTemplate[BaseSensor]):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hardware_choices = sensor_models
        loop_period = float(self.ecosystem.engine.config.app_config.SENSORS_LOOP_PERIOD)
        self._loop_period: float = max(loop_period, 10.0)
        self._slow_sensor_futures: set[_SensorFuture] = set()
        self._sensors_data: gv.SensorsData | gv.Empty = gv.Empty()
        #self._data_lock = Lock()
        self._sending_data_task: Task | None = None
        self._climate_routine_counter: int = 0
        self._climate_routine_task: Task | None = None
        self._finish__init__()

    @property
    def _climate_routine_ratio(self) -> int:
        climate_loop_period: float = self.ecosystem.subroutines["climate"]._loop_period
        return floor(max(1.0, climate_loop_period / self._loop_period))

    async def _routine(self) -> None:
        start_time = monotonic()
        self.logger.debug("Starting sensors data update routine ...")
        try:
            await self.update_sensors_data()
        except Exception as e:
            self.logger.error(
                f"Encountered an error while updating sensors data. "
                f"ERROR msg: `{e.__class__.__name__} :{e}`."
            )
        finally:
            update_time = monotonic() - start_time
            self.logger.debug(f"Sensors data update finished in {update_time:.1f} s.")
        if self.ecosystem.engine.use_message_broker:
            try:
                await self.schedule_send_data()
            except Exception as e:
                self.logger.error(
                    f"Encountered an error while sending sensors data and warnings. "
                    f"ERROR msg: `{e.__class__.__name__} :{e}`."
                )
        loop_time = monotonic() - start_time
        if loop_time > self._loop_period:  # pragma: no cover
            self.logger.warning(
                f"Sensors data routine took {loop_time:.1f}. This either "
                f"indicates errors while data retrieval or the need to "
                f"adapt 'SENSOR_LOOP_PERIOD'."
            )
        if self.ecosystem.get_subroutine_status("climate"):
            await self.trigger_climate_routine()

    def _compute_if_manageable(self) -> bool:
        if self.ecosystem.get_hardware_group_uids(gv.HardwareType.sensor):
            return True
        else:
            self.logger.warning("No sensor detected.")
            return False

    async def _start(self) -> None:
        self.logger.info(
            f"Starting the sensors loop. It will run every "
            f"{self._loop_period:.1f} s.")
        self.ecosystem.engine.scheduler.add_job(
            func=self.routine,
            id=f"{self.ecosystem.uid}-sensors_routine",
            trigger=IntervalTrigger(
                seconds=self._loop_period,
                jitter=self._loop_period / 10,
            ),
        )
        self.logger.debug("Sensors loop successfully started.")

    async def _stop(self) -> None:
        self.logger.info("Stopping sensors loop.")
        if self.ecosystem.get_subroutine_status("climate"):
            await self.ecosystem.stop_subroutine("climate")
        self.ecosystem.engine.scheduler.remove_job(
            f"{self.ecosystem.uid}-sensors_routine")
        self._sending_data_task = None

    """API calls"""
    def get_hardware_needed_uid(self) -> set[str]:
        return set(self.ecosystem.get_hardware_group_uids(gv.HardwareType.sensor))

    async def refresh(self) -> None:
        await super().refresh()
        # Make sure the routine is still running
        if not self.started:
            return
        # Refresh climate and light subroutines if they are running
        if self.ecosystem.get_subroutine_status("climate"):
            climate_subroutine: Climate = self.ecosystem.subroutines["climate"]
            await climate_subroutine.refresh()
        if self.ecosystem.get_subroutine_status("light"):
            light_subroutine: Light = self.ecosystem.subroutines["light"]
            light_subroutine.reset_light_sensors()

    @property
    def sensors_data(self) -> gv.SensorsData | gv.Empty:
        #async with self._data_lock:
        return self._sensors_data

    @sensors_data.setter
    def sensors_data(self, data: gv.SensorsData | gv.Empty) -> None:
        #async with self._data_lock:
        self._sensors_data = data

    async def _add_sensor_records(
            self,
            cache: gv.SensorsDataDict,
    ) -> gv.SensorsDataDict:
        slow_sensors: list[str] = [
            future.hardware_uid
            for future in self._slow_sensor_futures
        ]
        futures: list[_SensorFuture] = []
        for hardware in self.hardware.values():
            # Do not try to get data from sensors still trying to get their measures
            if hardware.uid in slow_sensors:
                continue
            future = asyncio.create_task(
                hardware.get_data(),
                name=f"{self.ecosystem.uid}-sensors-{hardware.uid}-get_data"
            )
            future = cast(_SensorFuture, future)
            future.hardware_uid = hardware.uid
            futures.append(future)
        # Try to get data from sensors that took too long during last loop
        futures.extend(self._slow_sensor_futures)
        # Wait for 5 secs for sensors to get data. This allows GPIO sensors to fail once
        done, pending = await asyncio.wait(futures, timeout=5)
        new_slow_futures = pending - self._slow_sensor_futures
        # Log the sensors that took too long
        for future in new_slow_futures:
            self.logger.warning(
                f"Sensor with uid '{future.hardware_uid}' took too long to "
                f"fetch data. Will try to gather data during next routine.")
        self._slow_sensor_futures = pending
        # Gather the data
        sensors_data: list[list[gv.SensorRecord]] = [future.result() for future in done]
        for sensor_data in sensors_data:
            cache["records"].extend(
                sensor_record
                for sensor_record in sensor_data
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
                timestamp=None,
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
            if record.measure not in parameter_limits:
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
            sensor_warnings.append(
                gv.SensorAlarm(
                    sensor_uid=record.sensor_uid,
                    measure=record.measure,
                    position=direction,
                    delta=delta,
                    level=level,
                )
            )
        cache["alarms"] = sensor_warnings
        return cache

    async def update_sensors_data(self) -> None:
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
        cache = await self._add_sensor_records(cache)
        cache = self._add_sensor_averages(cache)
        alarms_flag = gv.ManagementFlags.alarms
        if (self.config.management_flag & alarms_flag) == alarms_flag:
            cache = self._add_sensor_warnings(cache)
        if len(cache["records"]) > 0:
            self.sensors_data = gv.SensorsData(**cache)
        else:
            self.sensors_data = gv.Empty()

    async def send_data(self) -> None:
        # Check if we use the message broker
        if not self.ecosystem.engine.use_message_broker:
            return

        await self.ecosystem.engine.event_handler.send_payload_if_connected(
            "sensors_data", ecosystem_uids=[self.ecosystem.uid])

    async def schedule_send_data(self) -> None:
        if not (
                self._sending_data_task is None
                or self._sending_data_task.done()
        ):
            self.logger.warning(
                "There is already a sensors data sending task running. It will "
                "be cancelled to start a new one."
            )
            self._sending_data_task.cancel()
        self._sending_data_task = asyncio.create_task(
            self.send_data(), name=f"{self.ecosystem.uid}-sensors-send_data")

    async def trigger_climate_routine(self) -> None:
        if self._climate_routine_counter % self._climate_routine_ratio == 0:
            self._climate_routine_counter = 0
            self._climate_routine_task = asyncio.create_task(
                self.ecosystem.subroutines["climate"].routine(),
                name=f"{self.ecosystem.uid}-climate-routine",
            )
        self._climate_routine_counter += 1
