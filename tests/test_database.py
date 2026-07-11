from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import select

import gaia_validators as gv
from sqlalchemy_wrapper import AsyncSQLAlchemyWrapper

from gaia import Ecosystem, EngineConfig, Engine
from gaia.database import db as gaia_db
from gaia.database.models import SensorBuffer, SensorRecord
from gaia.database.routines import log_sensors_data

from tests import data as test_data


def generate_sensor_data(timestamp: datetime | None = None) -> dict:
    return {
        "sensor_uid": test_data.sensor_uid,
        "ecosystem_uid": test_data.ecosystem_uid,
        "measure": "temperature",
        "timestamp": timestamp or datetime.now().astimezone(timezone.utc),
        "value": 42,
    }


@pytest_asyncio.fixture(scope="session")
async def db(engine_config_master: EngineConfig) -> AsyncGenerator[AsyncSQLAlchemyWrapper]:
    dict_cfg = {
        key: getattr(engine_config_master.app_config, key)
        for key in dir(engine_config_master.app_config)
        if key.isupper()
    }
    gaia_db.init(dict_cfg)
    await gaia_db.create_all()

    yield gaia_db


@pytest_asyncio.fixture(scope="function")
async def engine_with_db(engine: Engine, db: AsyncSQLAlchemyWrapper) -> AsyncGenerator[Engine]:
    engine._db = db

    yield engine

    engine._db = None


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

        await SensorBuffer.mark_exchange_as_success(session, uuid)
        empty = True
        sensor_buffer = await SensorBuffer.get_buffered_data(session)
        async for _ in sensor_buffer:
            empty = False
            break
        assert empty


@pytest.mark.asyncio
async def test_log_sensors_data(
        db: AsyncSQLAlchemyWrapper,
        engine_with_db: Engine,
        ecosystem: Ecosystem,
        monkeypatch: pytest.MonkeyPatch,
):
    # Store the state
    db_management = ecosystem.config.get_management("database")

    # Set everything to the desired state
    ecosystem.config.set_management("database", True)

    # Patch sensors_data
    sensors_subroutine = ecosystem.get_subroutine("sensors")
    monkeypatch.setattr(sensors_subroutine, "_started", True)
    sensors_data: gv.SensorsData = gv.SensorsData(**{
        "timestamp": datetime.now(timezone.utc),
        "records": [gv.SensorRecord(test_data.sensor_uid, "temperature", 42.0)],
        "average": [gv.MeasureAverage("temperature", 42.0)],
        "alarms": [],
    })
    monkeypatch.setattr(sensors_subroutine, "sensors_data", sensors_data)

    # Test the DB routine
    await log_sensors_data(engine_with_db)

    # Make sure we logged something
    async with db.scoped_session() as session:
        # SensorRecord
        stmt = select(SensorRecord)
        result = await session.execute(stmt)
        records = result.all()
        assert len(records) > 0
        # SensorBuffer
        stmt = select(SensorBuffer)
        result = await session.execute(stmt)
        buffers = result.all()
        assert len(buffers) > 0

    # Restore the previous state
    ecosystem.config.set_management("database", db_management)
