from __future__ import annotations

from datetime import datetime
from typing import AsyncGenerator, NamedTuple, Sequence, Type, TypeVar
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

import gaia_validators as gv
from sqlalchemy_wrapper import AsyncSQLAlchemyWrapper

from gaia.utils import json


BT = TypeVar("BT", bound=gv.BufferedDataPayload)


db = AsyncSQLAlchemyWrapper(
    engine_options={
        "json_serializer": json.dumps,
        "json_deserializer": json.loads,
    },
)
Base = db.Model


class DataBufferMixin(Base):
    __abstract__ = True

    exchange_uuid: Mapped[UUID | None] = mapped_column()

    @classmethod
    async def get_buffered_data(
            cls,
            session: AsyncSession,
            per_page: int = 50,
    ) -> AsyncGenerator[gv.BufferedDataPayload]:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )  # pragma: no cover

    @classmethod
    async def _get_buffered_data(
            cls,
            buffered_record_class: Type[NamedTuple],
            buffered_payload_model: Type[BT],
            session: AsyncSession,
            per_page: int = 50,
    ) -> AsyncGenerator[BT]:
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
                buffered_data: Sequence[DataBufferMixin] = result.scalars().all()
                if not buffered_data:
                    break
                uuid = uuid4()
                rv: list[NamedTuple] = []
                for data in buffered_data:
                    data.exchange_uuid = uuid
                    rv.append(
                        buffered_record_class(**{
                            data_field: getattr(data, data_field)
                            for data_field in buffered_record_class._fields
                        })
                    )

                yield buffered_payload_model(
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

    @classmethod
    async def reset_exchange_uuids(cls, session: AsyncSession) -> None:
        stmt = (
            update(cls)
            .values(exchange_uuid=None)
        )
        await session.execute(stmt)


class BaseSensorRecord(Base):
    __abstract__ = True

    id: Mapped[int] = mapped_column(nullable=False, primary_key=True)
    ecosystem_uid: Mapped[str] = mapped_column(sa.String(length=8))
    sensor_uid: Mapped[str] = mapped_column(sa.String(length=16))
    measure: Mapped[str] = mapped_column(sa.String(length=16))
    timestamp: Mapped[datetime] = mapped_column()
    value: Mapped[float] = mapped_column(sa.Float(precision=2))

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


class SensorBuffer(BaseSensorRecord, DataBufferMixin):
    __tablename__ = "sensor_buffers"

    @classmethod
    async def get_buffered_data(
            cls,
            session: AsyncSession,
            per_page: int = 50
    ) -> AsyncGenerator[gv.BufferedSensorsDataPayload]:
        return cls._get_buffered_data(
            buffered_record_class=gv.BufferedSensorRecord,
            buffered_payload_model=gv.BufferedSensorsDataPayload,
            session=session,
            per_page=per_page,
        )


class BaseActuatorRecord(Base):
    __abstract__ = True

    id: Mapped[int] = mapped_column(primary_key=True)
    ecosystem_uid: Mapped[str] = mapped_column(sa.String(length=8))
    type: Mapped[gv.HardwareType] = mapped_column()
    timestamp: Mapped[datetime] = mapped_column()
    active: Mapped[bool] = mapped_column()
    mode: Mapped[gv.ActuatorMode] = mapped_column(default=gv.ActuatorMode.automatic)
    status: Mapped[bool] = mapped_column()
    level: Mapped[float | None] = mapped_column(default=None)

    __table_args__ = (
        sa.schema.UniqueConstraint(
            "type", "ecosystem_uid", "timestamp", "mode", "status",
            name="_no_repost_constraint"),
    )


class ActuatorRecord(BaseActuatorRecord):
    __tablename__ = "actuator_records"


class ActuatorBuffer(BaseActuatorRecord, DataBufferMixin):
    __tablename__ = "actuator_buffers"

    exchange_uuid: Mapped[UUID | None] = mapped_column()

    @classmethod
    async def get_buffered_data(
            cls,
            session: AsyncSession,
            per_page: int = 50
    ) -> AsyncGenerator[gv.BufferedActuatorsStatePayload]:
        return cls._get_buffered_data(
            buffered_record_class=gv.BufferedActuatorRecord,
            buffered_payload_model=gv.BufferedActuatorsStatePayload,
            session=session,
            per_page=per_page,
        )
