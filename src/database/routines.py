from datetime import timezone
import typing as t

from .models import SensorHistory
from config import Config


if t.TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import scoped_session

    from src.engine import Engine


try:
    sensors_logging_period = Config.SENSORS_LOGGING_PERIOD
except AttributeError:
    sensors_logging_period = 10


def log_sensors_data(scoped_session: "scoped_session", engine: "Engine"):
    with scoped_session() as session:
        for ecosystem_uid, ecosystem in engine.ecosystems.items():
            sensors_data = ecosystem.sensors_data
            if sensors_data:
                measurement_time: datetime = sensors_data["datetime"]
                measurement_time = measurement_time.astimezone(timezone.utc)
                if measurement_time.minute % sensors_logging_period == 0:
                    for sensor in sensors_data["data"]:
                        sensor_uid = sensor["sensor_uid"]
                        for measure in sensor["measures"]:
                            data_point = SensorHistory(
                                sensor_uid=sensor_uid,
                                ecosystem_uid=ecosystem_uid,
                                measure=measure["name"],
                                datetime=measurement_time,
                                value=measure["value"],
                            )
                            session.add(data_point)
        session.commit()
