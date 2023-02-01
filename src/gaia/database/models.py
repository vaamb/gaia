from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SensorHistory(Base):
    __tablename__ = "sensors_history"

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
            "datetime": self.timestamp,
            "value": self.value,
        }
