from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

import gaia_validators as gv
from sqlalchemy_wrapper import AsyncSQLAlchemyWrapper

from gaia import EngineConfig
from gaia.database import db as gaia_db
from gaia.database.models import SensorBuffer, SensorRecord

from .data import ecosystem_uid, sensor_uid


def generate_sensor_data(timestamp: datetime | None = None) -> dict:
    return {
        "sensor_uid": sensor_uid,
        "ecosystem_uid": ecosystem_uid,
        "measure": "temperature",
        "timestamp": timestamp or datetime.now().astimezone(timezone.utc),
        "value": 42,
    }


@pytest_asyncio.fixture(scope="session")
async def db(engine_config_master: EngineConfig) -> AsyncSQLAlchemyWrapper:
    dict_cfg = {
        key: getattr(engine_config_master.app_config, key)
        for key in dir(engine_config_master.app_config)
        if key.isupper()
    }
    gaia_db.init(dict_cfg)
    await gaia_db.create_all()

    yield gaia_db


@pytest.mark.asyncio
async def test_record(db: AsyncSQLAlchemyWrapper):
    async with db.scoped_session() as session:
        sensor_record = SensorRecord(**generate_sensor_data())
        session.add(sensor_record)
        await session.commit()
        stmt = select(SensorRecord)
        result = await session.execute(stmt)
        from_db = result.scalars().first()
        assert from_db.dict_repr == sensor_record.dict_repr


@pytest.mark.asyncio
async def test_buffer(db: AsyncSQLAlchemyWrapper):
    timestamp = datetime.now().astimezone(timezone.utc)
    async with db.scoped_session() as session:
        buffer_1 = SensorBuffer(**generate_sensor_data(timestamp - timedelta(minutes=5)))
        session.add(buffer_1)
        buffer_2 = SensorBuffer(**generate_sensor_data(timestamp))
        session.add(buffer_2)
        await session.commit()
        uuid = None
        sensor_buffer = await SensorBuffer.get_buffered_data(session)
        async for buffered_data in sensor_buffer:
            uuid = buffered_data.uuid
            data_1 = buffered_data.data[0]
            data_2 = buffered_data.data[1]
            for (buffer, data) in zip([buffer_1, buffer_2], [data_1, data_2]):
                buffer: SensorBuffer
                data: gv.BufferedSensorRecord
                assert buffer.sensor_uid == data.sensor_uid
                assert buffer.ecosystem_uid == data.ecosystem_uid
                assert buffer.measure == data.measure
                assert buffer.timestamp == data.timestamp
                assert buffer.value == data.value

        await SensorBuffer.clear_buffer(session, uuid)
        empty = True
        sensor_buffer = await SensorBuffer.get_buffered_data(session)
        async for _ in sensor_buffer:
            empty = False
            break
        assert empty


def test_log_sensors_data():
    #TODO
    pass
