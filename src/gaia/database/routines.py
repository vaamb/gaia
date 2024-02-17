from datetime import datetime, timezone
import typing as t
from typing import Callable

from sqlalchemy.orm import scoped_session, Session

import gaia_validators as gv

from gaia.database.models import SensorBuffer, SensorRecord
from gaia.utils import humanize_list


if t.TYPE_CHECKING:
    from gaia.engine import Engine


def log_sensors_data(
        scoped_session_: Callable[..., scoped_session],
        engine: "Engine"
) -> None:
    logged_ecosystem: set[str] = set()
    sensors_logging_period = engine.config.app_config.SENSORS_LOGGING_PERIOD
    with scoped_session_() as session:
        session: Session
        for ecosystem_uid, ecosystem in engine.ecosystems.items():
            sensors_data = ecosystem.sensors_data
            database_management = ecosystem.config.get_management("database")
            if isinstance(sensors_data, gv.SensorsData) and database_management:
                logged_ecosystem.add(ecosystem_uid)
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
    if logged_ecosystem:
        engine.logger.info(
            f"Logged sensors data for {humanize_list(list(logged_ecosystem))}.")
