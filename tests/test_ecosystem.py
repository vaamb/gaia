import typing as t

import pytest

from .utils import ECOSYSTEM_UID

if t.TYPE_CHECKING:
    from src import SpecificConfig
    from src import Ecosystem
    from src import Engine


def test_properties(
        ecosystem: "Ecosystem",
        specific_config: "SpecificConfig",
        engine: "Engine"
):
    assert ecosystem.uid == ECOSYSTEM_UID
    assert ecosystem.name == "test"
    assert ecosystem.status is False
    assert ecosystem.config.__dict__ == specific_config.__dict__
    assert ecosystem.engine.__dict__ == engine.__dict__
    assert ecosystem.subroutines_started == set()
    for management in ecosystem.management.values():
        assert management is False
    assert ecosystem.hardware == {}


def test_refresh_chaos(ecosystem: "Ecosystem"):
    ecosystem.refresh_chaos()


def test_start_stop(ecosystem: "Ecosystem"):
    ecosystem.start()
    ecosystem.stop()


def test_actuators(ecosystem: "Ecosystem"):
    ecosystem.turn_actuator("light", "automatic")
    ecosystem.turn_actuator("light", "on")
    ecosystem.turn_actuator("light", "off")
    with pytest.raises(ValueError):
        ecosystem.turn_actuator("WrongActuator", "on")
        ecosystem.turn_actuator("light", "WrongMode")


def test_light_calls(ecosystem: "Ecosystem"):
    assert ecosystem.light_info == {}
    with pytest.raises(RuntimeError):
        ecosystem.refresh_sun_times()


def test_sensors_calls(ecosystem: "Ecosystem"):
    assert ecosystem.sensors_data == {}


def test_health_calls(ecosystem: "Ecosystem"):
    assert ecosystem.plants_health == {}
