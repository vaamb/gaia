from datetime import datetime, timezone

import pytest

from src.database import SQLAlchemyWrapper
from src.database.models import SensorHistory
from config import Config


@pytest.fixture
def db():
    db = SQLAlchemyWrapper(Config)
    db.create_all()
    yield db
    db.drop_all()


def test_need_db_initialised():
    db = SQLAlchemyWrapper()
    with pytest.raises(RuntimeError):
        db.session


def test_insert_data(db: SQLAlchemyWrapper):
    with db.scoped_session() as session:
        data_point = SensorHistory(
            sensor_uid="uid",
            ecosystem_uid="uid",
            measure="temperature",
            datetime=datetime.now().astimezone(timezone.utc),
            value=10,
        )
        session.add(data_point)
        session.commit()
        from_db = session.query(SensorHistory).first()
        assert from_db.dict_repr == data_point.dict_repr
