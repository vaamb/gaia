import asyncio
import pytest

import gaia_validators as gv

from gaia import Ecosystem, EcosystemConfig, Engine, EngineConfig
from gaia.config import defaults
from gaia.exceptions import HardwareNotFound, NonValidSubroutine
from gaia.hardware.abc import _MetaHardware

from . import data
from .utils import get_logs_content


def test_properties(
        ecosystem: Ecosystem,
        ecosystem_config: EcosystemConfig,
        engine: Engine,
):
    assert ecosystem.uid == data.ecosystem_uid
    assert ecosystem.name == data.ecosystem_name
    assert ecosystem.started is False
    assert ecosystem.config.__dict__ is ecosystem_config.__dict__
    assert ecosystem.engine.__dict__ is engine.__dict__
    assert ecosystem.subroutines_started == set()


@pytest.mark.asyncio
async def test_ecosystem_states(ecosystem: "Ecosystem"):
    assert not ecosystem.started

    await ecosystem.start()
    assert ecosystem.started
    with get_logs_content(ecosystem.engine.config.logs_dir / data.debug_log_file) as logs:
        assert "Ecosystem successfully started" in logs
    with pytest.raises(RuntimeError, match=r"Ecosystem .* is already running"):
        await ecosystem.start()

    await ecosystem.stop()
    assert not ecosystem.started
    with get_logs_content(ecosystem.engine.config.logs_dir / data.debug_log_file) as logs:
        assert "Ecosystem successfully stopped" in logs
    with pytest.raises(
        RuntimeError, match=r"Cannot stop an ecosystem that hasn't started"):
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


@pytest.mark.asyncio
async def test_hardware(ecosystem: Ecosystem, engine_config: EngineConfig):
    # This test requires empty hardware
    for hardware_uid in [*ecosystem.hardware.keys()]:
        await ecosystem.remove_hardware(hardware_uid)

    await ecosystem.add_hardware(data.hardware_uid)
    with get_logs_content(engine_config.logs_dir / data.debug_log_file) as logs:
        assert f"Hardware {data.hardware_info['name']} has been set up." in logs

    with pytest.raises(ValueError, match=r"Hardware .* is already mounted."):
        await ecosystem.add_hardware(data.hardware_uid)

    await ecosystem.remove_hardware(data.hardware_uid)
    with get_logs_content(engine_config.logs_dir / data.debug_log_file) as logs:
        assert f"Hardware {data.hardware_info['name']} has been dismounted." in logs

    with pytest.raises(HardwareNotFound, match=f"Hardware '{data.hardware_uid}' not found."):
        await ecosystem.remove_hardware(data.hardware_uid)


@pytest.mark.asyncio
async def test_refresh(ecosystem: Ecosystem):
    hardware_needed: set[str] = {
        data.camera_uid,
        data.heater_uid,
        data.humidifier_uid,
        data.light_uid,
        data.sensor_uid,
        data.i2c_sensor_ens160_uid,
        data.i2c_sensor_veml7700_uid,
        data.ws_switch_uid,
        data.ws_dimmer_uid,
        data.ws_sensor_uid,
    }

    assert {*ecosystem.hardware.keys()} == hardware_needed

    # Make sure refresh_hardware adds the hardware needed ...
    await ecosystem.remove_hardware(data.sensor_uid)
    # The only reference to this hardware should be gone and hence be collected
    assert data.sensor_uid not in _MetaHardware.instances
    # ... removes the unneeded hardware ...
    ecosystem.config.delete_hardware(data.heater_uid)
    assert {*ecosystem.hardware.keys()} != hardware_needed
    # ... refresh the hardware whose config has changed
    ecosystem.config.IO_dict[data.light_uid]["level"] = gv.HardwareLevel.plants
    light_cfg = ecosystem.config.IO_dict[data.light_uid]
    outdated_cfg = ecosystem.hardware[data.light_uid].dict_repr()
    assert gv.to_anonymous(outdated_cfg, "uid") != light_cfg
    await ecosystem.refresh_hardware()

    # "A0oZpCJ50D0ajfJs" was removed from the config
    assert {*ecosystem.hardware.keys()} == hardware_needed - {"A0oZpCJ50D0ajfJs"}

    uptodate_cfg = ecosystem.hardware[data.light_uid].dict_repr()
    assert gv.to_anonymous(uptodate_cfg, "uid") == light_cfg

    # Refreshing a second time should not raise an exception
    await ecosystem.refresh_hardware()


def test_actuators_data(ecosystem: "Ecosystem"):
    actuator_states = ecosystem.actuator_hub.as_dict()
    assert len(actuator_states) == len(defaults.actuator_to_parameter)
    for actuator in actuator_states.values():
        assert not actuator["active"]
        assert not actuator["status"]
        assert actuator["mode"] is gv.ActuatorMode.automatic


@pytest.mark.asyncio
async def test_turn_actuator(ecosystem: "Ecosystem"):
    with pytest.raises(ValueError, match=r"Actuator group 'light' is not mounted."):
        await ecosystem.turn_actuator("light", gv.ActuatorModePayload.automatic)

    # All subroutines are disabled by default in testing config
    await ecosystem.enable_subroutine("light")
    await ecosystem.start_subroutine("light")

    actuator_handler = ecosystem.actuator_hub.get_handler("light")

    await ecosystem.turn_actuator("light", gv.ActuatorModePayload.on)
    assert actuator_handler.mode is gv.ActuatorMode.manual
    assert actuator_handler.status
    assert actuator_handler.level == 100.0

    await ecosystem.turn_actuator("light", gv.ActuatorModePayload.off)
    assert not actuator_handler.status

    await ecosystem.turn_actuator("light", gv.ActuatorModePayload.automatic)
    assert actuator_handler.mode is gv.ActuatorMode.automatic

    await ecosystem.turn_actuator("light", gv.ActuatorModePayload.on, countdown=0.25)
    assert actuator_handler.mode is gv.ActuatorMode.automatic
    await asyncio.sleep(0.3)
    assert actuator_handler.status
    assert actuator_handler.mode is gv.ActuatorMode.manual

    await ecosystem.turn_actuator("light", gv.ActuatorModePayload.on, level=75.0)
    assert actuator_handler.level == 75.0

    await ecosystem.turn_actuator("light", gv.ActuatorModePayload.automatic)

    with pytest.raises(ValueError):
        await ecosystem.turn_actuator(gv.HardwareType.light, "WrongMode")

    await ecosystem.stop_subroutine("light")
