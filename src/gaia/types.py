from typing import NamedTuple


class SensorData(NamedTuple):
    sensor_uid: str
    measure: str
    value: float | None
