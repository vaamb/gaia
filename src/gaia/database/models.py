from __future__ import annotations

from datetime import datetime
from typing import AsyncGenerator, Sequence
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

import gaia_validators as gv
from sqlalchemy_wrapper import AsyncSQLAlchemyWrapper

from gaia.utils import json


db = AsyncSQLAlchemyWrapper(
    engine_options={
        "json_serializer": json.dumps,
        "json_deserializer": json.loads,
    },
)
Base = db.Model


class BaseSensorRecord(Base):
    __abstract__ = True

    id: Mapped[int] = mapped_column(nullable=False, primary_key=True)
    sensor_uid: Mapped[str] = mapped_column(sa.String(length=16), nullable=False)
    ecosystem_uid: Mapped[str] = mapped_column(sa.String(length=8), nullable=False)
    measure: Mapped[str] = mapped_column(sa.String(length=16), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(nullable=False)
    value: Mapped[float] = mapped_column(sa.Float(precision=2), nullable=False)

    __table_args__ = (
        sa.schema.UniqueConstraint(
            "measure", "timestamp", "value", "ecosystem_uid", "sensor_uid",
            name="_no_repost_constraint"),
    )

    @property
    def dict_repr(self) -> dict:
        return {
            "sensor_uid": self.sensor_uid,
            "ecosystem_uid": self.ecosystem_uid,
            "measure": self.measure,
            "timestamp": self.timestamp,
            "value": self.value,
        }


class SensorRecord(BaseSensorRecord):
    __tablename__ = "sensor_records"


class SensorBuffer(BaseSensorRecord):
    __tablename__ = "sensor_buffers"

    exchange_uuid: Mapped[UUID | None] = mapped_column()

    @classmethod
    async def get_buffered_data(
            cls,
            session: AsyncSession,
            per_page: int = 50
    ) -> AsyncGenerator[gv.BufferedSensorsDataPayload]:
        page: int = 0
        try:
            while True:
                stmt = (
                    select(cls)
                    .where(cls.exchange_uuid == None)
                    .offset(per_page * page)
                    .limit(per_page)
                )
                result = await session.execute(stmt)
                buffered_data: Sequence[SensorBuffer] = result.scalars().all()
                if not buffered_data:
                    break
                uuid = uuid4()
                rv: list[gv.BufferedSensorRecord] = []
                for data in buffered_data:
                    data.exchange_uuid = uuid
                    rv.append(
                        gv.BufferedSensorRecord(
                            ecosystem_uid=data.ecosystem_uid,
                            sensor_uid=data.sensor_uid,
                            measure=data.measure,
                            value=data.value,
                            timestamp=data.timestamp
                        )
                    )

                yield gv.BufferedSensorsDataPayload(
                    data=rv,
                    uuid=uuid,
                )
                page += 1
        finally:
            await session.commit()

    @classmethod
    async def clear_buffer(cls, session: AsyncSession, uuid: UUID | str) -> None:
        stmt = (
            delete(cls)
            .where(cls.exchange_uuid == uuid)
        )
        await session.execute(stmt)

    @classmethod
    async def clear_uuid(cls, session: AsyncSession, uuid: UUID | str) -> None:
        stmt = (
            update(cls)
            .where(cls.exchange_uuid == uuid)
            .values(exchange_uuid=None)
        )
        await session.execute(stmt)
