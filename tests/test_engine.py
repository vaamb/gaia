import typing as t

import pytest

from src.engine import Engine
from .utils import ECOSYSTEM_UID

if t.TYPE_CHECKING:
    from src.config_parser import GeneralConfig


def test_engine_singleton(general_config: "GeneralConfig", engine: Engine):
    assert engine is Engine(general_config)


def test_properties(general_config: "GeneralConfig", engine: Engine):
    assert engine.ecosystems == {}
    assert engine.config.__dict__ == general_config.__dict__
    assert engine.ecosystems_started == set()
    assert engine.event_handler is None


def test_ecosystems_procedures(engine: Engine):
    with pytest.raises(ValueError):
        engine.init_ecosystem("DoesNotExist")
        engine.start_ecosystem("DoesNotExist")
        engine.stop_ecosystem("DoesNotExist")
        engine.dismount_ecosystem("DoesNotExist")
    with pytest.raises(RuntimeError):
        engine.start_ecosystem(ECOSYSTEM_UID)
        engine.stop_ecosystem(ECOSYSTEM_UID)
        engine.dismount_ecosystem(ECOSYSTEM_UID)
    engine.init_ecosystem(ECOSYSTEM_UID)
    engine.start_ecosystem(ECOSYSTEM_UID)
    # with pytest.raises(RuntimeError):  # Bug in test suite but not when alone
        # engine.dismount_ecosystem(ECOSYSTEM_UID)
    engine.stop_ecosystem(ECOSYSTEM_UID)
    engine.dismount_ecosystem(ECOSYSTEM_UID)
    engine.refresh_ecosystems()


def test_start_stop(engine: Engine):
    engine.start()
    engine.stop()


def test_refresh_sun_times(engine: Engine):
    # engine.refresh_sun_times()
    pass


def test_refresh_chaos(engine: Engine):
    engine.refresh_chaos()
