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


@pytest.mark.asyncio
async def test_ecosystem_states(ecosystem: "Ecosystem"):
    assert not ecosystem.started

    await ecosystem.start()
    assert ecosystem.started
    with get_logs_content(ecosystem.engine.config.logs_dir / "gaia.log") as logs:
        assert f"Ecosystem successfully started" in logs
    with pytest.raises(RuntimeError, match=r"Ecosystem .* is already running"):
        await ecosystem.start()

    await ecosystem.stop()
    assert not ecosystem.started
    with get_logs_content(ecosystem.engine.config.logs_dir / "gaia.log") as logs:
        assert f"Ecosystem successfully stopped" in logs
    with pytest.raises(RuntimeError, match=r"Cannot stop an ecosystem that hasn't started"):
        await ecosystem.stop()


@pytest.mark.asyncio
async def test_subroutine_management(ecosystem: "Ecosystem"):
    # Simply dispatches work to subroutine, methods are tested there

    await ecosystem.enable_subroutine("dummy")
    await ecosystem.start_subroutine("dummy")
    assert ecosystem.get_subroutine_status("dummy")
    assert ecosystem.subroutines_started == {"dummy"}
    await ecosystem.refresh_subroutines()
    await ecosystem.stop_subroutine("dummy")
    assert ecosystem.subroutines_started == set()
    await ecosystem.disable_subroutine("dummy")

    with pytest.raises(NonValidSubroutine, match=r"is not valid."):
        await ecosystem.enable_subroutine("WrongSubroutine")


def test_actuator_data(ecosystem: "Ecosystem"):
    actuator_states = ecosystem.actuator_data
    for actuator in actuator_states.values():
        assert not actuator["active"]
        assert not actuator["status"]
        assert actuator["mode"] is gv.ActuatorMode.automatic


@pytest.mark.asyncio
async def test_light_actuators(ecosystem: "Ecosystem"):
    with pytest.raises(ValueError, match=r"Light subroutine is not running"):
        await ecosystem.turn_actuator(gv.HardwareType.light, gv.ActuatorModePayload.automatic)

    # All subroutines are disabled by default in testing config
    await ecosystem.enable_subroutine("light")
    await ecosystem.start_subroutine("light")

    await ecosystem.turn_actuator(gv.HardwareType.light, gv.ActuatorModePayload.on)
    await ecosystem.turn_actuator(gv.HardwareType.light, gv.ActuatorModePayload.off)
    await ecosystem.turn_actuator(gv.HardwareType.light, gv.ActuatorModePayload.automatic)
    with pytest.raises(ValueError):
        await ecosystem.turn_actuator(gv.HardwareType.light, "WrongMode")

    await ecosystem.stop_subroutine("light")


@pytest.mark.asyncio
async def test_climate_actuators(ecosystem: "Ecosystem"):
    with pytest.raises(ValueError, match=r"Climate subroutine is not running"):
        await ecosystem.turn_actuator(gv.HardwareType.heater, gv.ActuatorModePayload.automatic)

    # Climate subroutine requires a working sensors subroutine ...
    await ecosystem.enable_subroutine("sensors")
    await ecosystem.start_subroutine("sensors")
    # ... and climatic parameters set
    ecosystem.config.set_climate_parameter(
        "temperature", day=25, night= 20, hysteresis=2)

    # All subroutines are disabled by default in testing config
    await ecosystem.enable_subroutine("climate")
    await ecosystem.start_subroutine("climate")

    await ecosystem.turn_actuator(gv.HardwareType.heater, gv.ActuatorModePayload.on)
    await ecosystem.turn_actuator(gv.HardwareType.heater, gv.ActuatorModePayload.off)
    await ecosystem.turn_actuator(gv.HardwareType.heater, gv.ActuatorModePayload.automatic)
    with pytest.raises(ValueError):
        await ecosystem.turn_actuator("WrongActuator", "on")

    await ecosystem.stop_subroutine("climate")
    await ecosystem.stop_subroutine("sensors")


def test_sensors_calls(ecosystem: "Ecosystem"):
    assert ecosystem.sensors_data == gv.Empty()


def test_health_calls(ecosystem: "Ecosystem"):
    assert ecosystem.plants_health == gv.Empty()


# TODO: add a test for setting light method
