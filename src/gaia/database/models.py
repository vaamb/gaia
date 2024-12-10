from __future__ import annotations

from datetime import datetime, timezone
from logging import getLogger, Logger
from typing import AsyncGenerator, NamedTuple, Self, Sequence, Type, TypeVar
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import delete, select, UniqueConstraint, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import DateTime, TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column

import gaia_validators as gv
from sqlalchemy_wrapper import AsyncSQLAlchemyWrapper

from gaia.utils import json


BT = TypeVar("BT", bound=gv.BufferedDataPayload)

db_logger: Logger = getLogger("gaia.engine.db")


db = AsyncSQLAlchemyWrapper(
    engine_options={
        "json_serializer": json.dumps,
        "json_deserializer": json.loads,
    },
)
Base = db.Model


class UtcDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if isinstance(value, datetime):
            return value.replace(tzinfo=timezone.utc)
        return value


class DataBufferMixin(Base):
    __abstract__ = True

    exchange_uuid: Mapped[UUID | None] = mapped_column()

    @property
    def dict_repr(self) -> dict:
        raise NotImplementedError("This method must be implemented in a subclass")  # pragma: no cover

    @classmethod
    async def get_buffered_data(
            cls,
            session: AsyncSession,
            per_page: int = 50,
    ) -> AsyncGenerator[gv.BufferedDataPayload]:
        raise NotImplementedError("This method must be implemented in a subclass")  # pragma: no cover

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
                # Get buffered data
                stmt = (
                    select(cls)
                    .where(cls.exchange_uuid == None)  # noqa: E711
                    .offset(per_page * page)
                    .limit(per_page)
                )
                result = await session.execute(stmt)
                buffered_data: Sequence[Self] = result.scalars().all()
                if not buffered_data:
                    break
                # Create an exchange uuid ...
                exchange_uuid = uuid4()
                # ... store it in the db ...
                stmt = (
                    update(cls)
                    .where(cls.id.in_([row.id for row in buffered_data]))
                    .values({
                        "exchange_uuid": exchange_uuid,
                    })
                )
                await session.execute(stmt)
                # ... and use it in the payload
                yield buffered_payload_model(
                    uuid=exchange_uuid,
                    data=[
                        buffered_record_class(**row.dict_repr)
                        for row in buffered_data
                    ]
                )
                await session.commit()
                page += 1
        except Exception as e:
            db_logger.error(
                f"Encountered an error while retrieving buffered data for "
                f"{cls.__name__}. ERROR msg: `{e.__class__.__name__} :{e}`.")
            await session.rollback()
            raise

    @classmethod
    async def mark_exchange_as_success(
            cls,
            session: AsyncSession,
            exchange_uuid: UUID | str,
    ) -> None:
        stmt = (
            delete(cls)
            .where(cls.exchange_uuid == exchange_uuid)
        )
        await session.execute(stmt)

    @classmethod
    async def mark_exchange_as_failed(
            cls,
            session: AsyncSession,
            exchange_uuid: UUID | str,
    ) -> None:
        stmt = (
            update(cls)
            .where(cls.exchange_uuid == exchange_uuid)
            .values({
                "exchange_uuid": None,
            })
        )
        await session.execute(stmt)

    @classmethod
    async def reset_ongoing_exchanges(cls, session: AsyncSession) -> None:
        stmt = (
            update(cls)
            .where(cls.exchange_uuid != None)  # noqa: E711
            .values({
                "exchange_uuid": None,
            })
        )
        await session.execute(stmt)


class BaseSensorRecord(Base):
    __abstract__ = True
    __table_args__ = (
        UniqueConstraint(
            "measure", "timestamp", "value", "ecosystem_uid", "sensor_uid",
            name="_uq_no_repost_constraint",
        ),
    )

    id: Mapped[int] = mapped_column(nullable=False, primary_key=True)
    ecosystem_uid: Mapped[str] = mapped_column(sa.String(length=8))
    sensor_uid: Mapped[str] = mapped_column(sa.String(length=16))
    measure: Mapped[str] = mapped_column(sa.String(length=16))
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime)
    value: Mapped[float] = mapped_column(sa.Float(precision=2))

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
            per_page: int = 50,
    ) -> AsyncGenerator[gv.BufferedSensorsDataPayload]:
        return cls._get_buffered_data(
            buffered_record_class=gv.BufferedSensorRecord,
            buffered_payload_model=gv.BufferedSensorsDataPayload,
            session=session,
            per_page=per_page,
        )


class BaseActuatorRecord(Base):
    __abstract__ = True
    __table_args__ = (
        UniqueConstraint(
            "ecosystem_uid", "type", "timestamp", "mode", "status",
            name="_uq_no_repost_constraint",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ecosystem_uid: Mapped[str] = mapped_column(sa.String(length=8))
    type: Mapped[gv.HardwareType] = mapped_column()
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime)
    active: Mapped[bool] = mapped_column()
    mode: Mapped[gv.ActuatorMode] = mapped_column(default=gv.ActuatorMode.automatic)
    status: Mapped[bool] = mapped_column()
    level: Mapped[float | None] = mapped_column(default=None)

    @property
    def dict_repr(self) -> dict:
        return {
            "ecosystem_uid": self.ecosystem_uid,
            "type": self.type,
            "active": self.active,
            "mode": self.mode,
            "status": self.status,
            "level": self.level,
            "timestamp": self.timestamp,
        }


class ActuatorRecord(BaseActuatorRecord):
    __tablename__ = "actuator_records"


class ActuatorBuffer(BaseActuatorRecord, DataBufferMixin):
    __tablename__ = "actuator_buffers"

    exchange_uuid: Mapped[UUID | None] = mapped_column()

    @classmethod
    async def get_buffered_data(
            cls,
            session: AsyncSession,
            per_page: int = 50,
    ) -> AsyncGenerator[gv.BufferedActuatorsStatePayload]:
        return cls._get_buffered_data(
            buffered_record_class=gv.BufferedActuatorRecord,
            buffered_payload_model=gv.BufferedActuatorsStatePayload,
            session=session,
            per_page=per_page,
        )


class HealthRecord(BaseSensorRecord):
    __tablename__ = "health_records"


class HealthBuffer(BaseSensorRecord, DataBufferMixin):
    __tablename__ = "health_buffers"

    exchange_uuid: Mapped[UUID | None] = mapped_column()

    @classmethod
    async def get_buffered_data(
            cls,
            session: AsyncSession,
            per_page: int = 50,
    ) -> AsyncGenerator[gv.BufferedHealthRecordPayload]:
        return cls._get_buffered_data(
            buffered_record_class=gv.BufferedSensorRecord,
            buffered_payload_model=gv.BufferedHealthRecordPayload,
            session=session,
            per_page=per_page,
        )
