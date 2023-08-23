from datetime import datetime, timezone
import typing as t

from gaia_validators import SensorsData

from gaia.config import get_config
from gaia.database.models import SensorBuffer, SensorRecord


if t.TYPE_CHECKING:
    from sqlalchemy.orm import scoped_session

    from gaia.engine import Engine


sensors_logging_period = get_config().SENSORS_LOGGING_PERIOD


def log_sensors_data(scoped_session: "scoped_session", engine: "Engine"):
    with scoped_session() as session:
        for ecosystem_uid, ecosystem in engine.ecosystems.items():
            sensors_data = ecosystem.sensors_data
            database_management = ecosystem.config.get_management("database")
            if isinstance(sensors_data, SensorsData) and database_management:
                timestamp: datetime = sensors_data.timestamp
                timestamp = timestamp.astimezone(timezone.utc)
                if timestamp.minute % sensors_logging_period == 0:
                    for sensor_record in sensors_data.records:
                        if sensor_record.value is None:
                            continue
                        formatted_data = {
                            "sensor_uid": sensor_record.sensor_uid,
                            "ecosystem_uid": ecosystem_uid,
                            "measure": sensor_record.measure,
                            "timestamp": timestamp,
                            "value": sensor_record.value,
                        }
                        sensor_record = SensorRecord(**formatted_data)
                        session.add(sensor_record)
                        if (
                                engine.use_message_broker
                                and not engine.event_handler.is_connected()
                        ):
                            sensor_buffer = SensorBuffer(**formatted_data)
                            session.add(sensor_buffer)
        session.commit()
