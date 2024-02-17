import pytest

import gaia_validators as gv

from gaia import Ecosystem, EcosystemConfig, Engine
from gaia.exceptions import NonValidSubroutine

from .data import ecosystem_uid, ecosystem_name
from .utils import get_logs_content


def test_properties(
        ecosystem: Ecosystem,
        ecosystem_config: EcosystemConfig,
        engine: Engine,
):
    assert ecosystem.uid == ecosystem_uid
    assert ecosystem.name == ecosystem_name
    assert ecosystem.started is False
    assert ecosystem.config.__dict__ is ecosystem_config.__dict__
    assert ecosystem.engine.__dict__ is engine.__dict__
    assert ecosystem.subroutines_started == set()


def test_ecosystem_states(ecosystem: "Ecosystem"):
    assert not ecosystem.started

    ecosystem.start()
    assert ecosystem.started
    with get_logs_content(ecosystem.engine.config.logs_dir / "gaia.log") as logs:
        assert f"Ecosystem successfully started" in logs
    with pytest.raises(RuntimeError, match=r"Ecosystem .* is already running"):
        ecosystem.start()

    ecosystem.stop()
    assert not ecosystem.started
    with get_logs_content(ecosystem.engine.config.logs_dir / "gaia.log") as logs:
        assert f"Ecosystem successfully stopped" in logs
    with pytest.raises(RuntimeError, match=r"Cannot stop an ecosystem that hasn't started"):
        ecosystem.stop()


def test_subroutine_management(ecosystem: "Ecosystem"):
    # Simply dispatches work to subroutine, methods are tested there

    ecosystem.enable_subroutine("dummy")
    ecosystem.start_subroutine("dummy")
    assert ecosystem.get_subroutine_status("dummy")
    assert ecosystem.subroutines_started == {"dummy"}
    ecosystem.refresh_subroutines()
    ecosystem.stop_subroutine("dummy")
    assert ecosystem.subroutines_started == set()
    ecosystem.disable_subroutine("dummy")

    with pytest.raises(NonValidSubroutine, match=r"is not valid."):
        ecosystem.enable_subroutine("WrongSubroutine")


def test_actuator_data(ecosystem: "Ecosystem"):
    actuator_states = ecosystem.actuator_data
    for actuator in actuator_states.values():
        assert not actuator["active"]
        assert not actuator["status"]
        assert actuator["mode"] is gv.ActuatorMode.automatic


def test_light_actuators(ecosystem: "Ecosystem"):
    with pytest.raises(ValueError, match=r"Light subroutine is not running"):
        ecosystem.turn_actuator(gv.HardwareType.light, gv.ActuatorModePayload.automatic)

    # All subroutines are disabled by default in testing config
    ecosystem.enable_subroutine("light")
    ecosystem.start_subroutine("light")

    ecosystem.turn_actuator(gv.HardwareType.light, gv.ActuatorModePayload.on)
    ecosystem.turn_actuator(gv.HardwareType.light, gv.ActuatorModePayload.off)
    ecosystem.turn_actuator(gv.HardwareType.light, gv.ActuatorModePayload.automatic)
    with pytest.raises(ValueError):
        ecosystem.turn_actuator(gv.HardwareType.light, "WrongMode")

    ecosystem.stop_subroutine("light")


def test_climate_actuators(ecosystem: "Ecosystem"):
    with pytest.raises(ValueError, match=r"Climate subroutine is not running"):
        ecosystem.turn_actuator(gv.HardwareType.heater, gv.ActuatorModePayload.automatic)

    # Climate subroutine requires a working sensors subroutine ...
    ecosystem.enable_subroutine("sensors")
    ecosystem.start_subroutine("sensors")
    # ... and climatic parameters set
    ecosystem.config.set_climate_parameter(
        "temperature", day=25, night= 20, hysteresis=2)

    # All subroutines are disabled by default in testing config
    ecosystem.enable_subroutine("climate")
    ecosystem.start_subroutine("climate")

    ecosystem.turn_actuator(gv.HardwareType.heater, gv.ActuatorModePayload.on)
    ecosystem.turn_actuator(gv.HardwareType.heater, gv.ActuatorModePayload.off)
    ecosystem.turn_actuator(gv.HardwareType.heater, gv.ActuatorModePayload.automatic)
    with pytest.raises(ValueError):
        ecosystem.turn_actuator("WrongActuator", "on")

    ecosystem.stop_subroutine("climate")
    ecosystem.stop_subroutine("sensors")


def test_sensors_calls(ecosystem: "Ecosystem"):
    assert ecosystem.sensors_data == gv.Empty()


def test_health_calls(ecosystem: "Ecosystem"):
    assert ecosystem.plants_health == gv.Empty()


# TODO: add a test for setting light method