from __future__ import annotations

from anyio.to_thread import run_sync

import gaia_validators as gv

from gaia.hardware.abc import BaseSensor, Measure, Unit
from gaia.utils import (
    get_absolute_humidity, get_dew_point, get_unit, temperature_converter)


class TempHumSensor(BaseSensor):
    measures_available = {
        Measure.absolute_humidity: Unit.gram_per_cubic_m,
        Measure.dew_point: Unit.celsius_degree,
        Measure.humidity: Unit.rel_humidity,
        Measure.temperature: Unit.celsius_degree,
    }

    def _get_raw_data(self) -> tuple[float | None, float | None]:
        raise NotImplementedError("This method must be implemented in a subclass")

    async def get_data(self) -> list[gv.SensorRecord]:
        raw_humidity, raw_temperature = await run_sync(self._get_raw_data)
        data = []
        if Measure.humidity in self.measures:
            data.append(
                gv.SensorRecord(
                    sensor_uid=self.uid,
                    measure="humidity",
                    value=raw_humidity,
                )
            )

        if Measure.temperature in self.measures:
            temperature = temperature_converter(
                raw_temperature, "celsius", get_unit("temperature", "celsius"))
            data.append(
                gv.SensorRecord(
                    sensor_uid=self.uid,
                    measure="temperature",
                    value=temperature,
                )
            )

        if Measure.dew_point in self.measures:
            raw_dew_point = get_dew_point(raw_temperature, raw_humidity)
            dew_point = temperature_converter(
                raw_dew_point, "celsius", get_unit("temperature", "celsius"))
            data.append(
                gv.SensorRecord(
                    sensor_uid=self.uid,
                    measure="dew_point",
                    value=dew_point,
                )
            )

        if Measure.absolute_humidity in self.measures:
            data.append(
                gv.SensorRecord(
                    sensor_uid=self.uid,
                    measure="absolute_humidity",
                    value=get_absolute_humidity(raw_temperature, raw_humidity),
                )
            )
        return data
