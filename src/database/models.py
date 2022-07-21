import sqlalchemy as sa
from sqlalchemy.orm import declarative_base


base = declarative_base()


class SensorHistory(base):
    __tablename__ = "sensors_history"

    id = sa.Column(sa.Integer, autoincrement=True, nullable=False, primary_key=True)
    sensor_uid = sa.Column(sa.String(length=16), nullable=False)
    ecosystem_uid = sa.Column(sa.String(length=8), nullable=False)
    measure = sa.Column(sa.Integer, nullable=False)
    datetime = sa.Column(sa.DateTime, nullable=False)
    value = sa.Column(sa.Float(precision=2), nullable=False)

    __table_args__ = (
        sa.schema.UniqueConstraint(
            "measure", "datetime", "value", "ecosystem_uid", "sensor_uid",
            name="_no_repost_constraint"),
    )
