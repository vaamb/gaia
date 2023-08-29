from __future__ import annotations

from gaia_validators import SensorRecord

from gaia.hardware.abc import BaseSensor
from gaia.utils import (
    get_absolute_humidity, get_dew_point, get_unit, temperature_converter)


class TempHumSensor(BaseSensor):
    def _get_raw_data(self) -> tuple[float | None, float | None]:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    def get_data(self) -> list[SensorRecord]:
        raw_humidity, raw_temperature = self._get_raw_data()
        data = []
        if "humidity" in self.measures:
            data.append(SensorRecord(
                sensor_uid=self.uid,
                measure="humidity",
                value=raw_humidity
            ))

        if "temperature" in self.measures:
            temperature = temperature_converter(
                raw_temperature, "celsius", get_unit("temperature", "celsius"))
            data.append(SensorRecord(
                sensor_uid=self.uid,
                measure="temperature",
                value=temperature
            ))

        if "dew_point" in self.measures:
            raw_dew_point = get_dew_point(raw_temperature, raw_humidity)
            dew_point = temperature_converter(
                raw_dew_point, "celsius", get_unit("temperature", "celsius"))
            data.append(SensorRecord(
                sensor_uid=self.uid,
                measure="dew_point",
                value=dew_point
            ))

        if "absolute_humidity" in self.measures:
            data.append(SensorRecord(
                sensor_uid=self.uid,
                measure="absolute_humidity",
                value=get_absolute_humidity(raw_temperature, raw_humidity)
            ))
        return data