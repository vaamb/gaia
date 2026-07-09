import asyncio
import pytest

import gaia_validators as gv

from gaia import Ecosystem, EcosystemConfig, Engine
from gaia.config import default_actuators
from gaia.exceptions import HardwareNotFound, SubroutineNotFound
from gaia.hardware import hardware_models
from gaia.hardware.abc import _MetaHardware
from gaia.hardware.camera import PiCamera

from tests import data as test_data


hardware_dict = {
    test_data.sensor_uid: test_data.sensor_info,
    test_data.light_uid: test_data.light_info,
    test_data.heater_uid: test_data.heater_info,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("ecosystem_config", [{"hardware": hardware_dict}], indirect=True)
class TestEcosystem:
    async def test_properties(
            self,
            ecosystem: Ecosystem,
            ecosystem_config: EcosystemConfig,
            engine: Engine,
    ):
        assert ecosystem.uid == test_data.ecosystem_uid
        assert ecosystem.name == test_data.ecosystem_name
        assert ecosystem.started is False
        assert ecosystem.config.__dict__ is ecosystem_config.__dict__
        assert ecosystem.engine.__dict__ is engine.__dict__
        assert ecosystem.subroutines_started == set()

    async def test_states(self, ecosystem: Ecosystem, caplog: pytest.LogCaptureFixture):
        assert not ecosystem.started

        await ecosystem.start()
        assert ecosystem.started
        assert "Ecosystem successfully started" in caplog.text
        with pytest.raises(RuntimeError, match=r"Ecosystem .* is already running"):
            await ecosystem.start()

        await ecosystem.stop()
        assert not ecosystem.started
        assert "Ecosystem successfully stopped" in caplog.text
        with pytest.raises(
            RuntimeError, match=r"Cannot stop an ecosystem that hasn't started"):
            await ecosystem.stop()

    async def test_subroutine_management(self, ecosystem: Ecosystem):
        # Simply dispatches work to subroutine, methods are tested there

        await ecosystem.enable_subroutine("light")
        await ecosystem.start_subroutine("light")
        assert ecosystem.get_subroutine_status("light")
        assert ecosystem.subroutines_started == {"light"}
        await ecosystem.refresh_subroutines()
        await ecosystem.stop_subroutine("light")
        assert ecosystem.subroutines_started == set()
        await ecosystem.disable_subroutine("light")

        with pytest.raises(SubroutineNotFound, match=r"is not valid."):
            await ecosystem.enable_subroutine("WrongSubroutine")

    async def test_refresh_subroutines_stops_all_when_none_needed(self, ecosystem: Ecosystem):
        await ecosystem.enable_subroutine("light")
        await ecosystem.refresh_subroutines()
        assert ecosystem.subroutines_started == {"light"}

        # Disabling the last enabled subroutine should stop it on the next refresh
        await ecosystem.disable_subroutine("light")
        await ecosystem.refresh_subroutines()
        assert ecosystem.subroutines_started == set()


    async def test_hardware(self, ecosystem: Ecosystem, caplog: pytest.LogCaptureFixture):
        # This test requires empty hardware
        for hardware_uid in [*ecosystem.hardware.keys()]:
            await ecosystem.remove_hardware(hardware_uid)

        await ecosystem.add_hardware(test_data.hardware_uid)
        assert f"Hardware {test_data.hardware_info['name']} has been set up." in caplog.text

        with pytest.raises(ValueError, match=r"Hardware .* is already mounted."):
            await ecosystem.add_hardware(test_data.hardware_uid)

        await ecosystem.remove_hardware(test_data.hardware_uid)
        assert f"Hardware {test_data.hardware_info['name']} has been dismounted." in caplog.text

        with pytest.raises(HardwareNotFound, match=f"Hardware '{test_data.hardware_uid}' not found."):
            await ecosystem.remove_hardware(test_data.hardware_uid)

    async def test_refresh_hardware(self, ecosystem: Ecosystem):
        hardware_needed: set[str] = set(hardware_dict.keys())

        assert {*ecosystem.hardware.keys()} == hardware_needed

        # Make sure refresh_hardware adds the hardware needed ...
        await ecosystem.remove_hardware(test_data.sensor_uid)
        # The only reference to this hardware should be gone and hence be collected
        assert test_data.sensor_uid not in _MetaHardware.instances
        # ... removes the unneeded hardware ...
        ecosystem.config.delete_hardware(test_data.heater_uid)
        assert {*ecosystem.hardware.keys()} != hardware_needed
        # ... refresh the hardware whose config has changed
        ecosystem.config.hardware_dict[test_data.light_uid]["level"] = gv.HardwareLevel.plants
        light_cfg = ecosystem.config.hardware_dict[test_data.light_uid]
        outdated_cfg = ecosystem.hardware[test_data.light_uid].dict_repr()
        assert gv.to_anonymous(outdated_cfg, "uid") != light_cfg
        await ecosystem.refresh_hardware()

        # "A0oZpCJ50D0ajfJs" was removed from the config
        assert {*ecosystem.hardware.keys()} == hardware_needed - {"A0oZpCJ50D0ajfJs"}

        uptodate_cfg = ecosystem.hardware[test_data.light_uid].dict_repr()
        assert gv.to_anonymous(uptodate_cfg, "uid") == light_cfg

        # Refreshing a second time should not raise an exception
        await ecosystem.refresh_hardware()

    async def test_refresh_hardware_resiliency(
            self,
            ecosystem: Ecosystem,
            monkeypatch: pytest.MonkeyPatch,
    ):
        # Fake camera that raises during init
        class CrashingCamera(PiCamera):
            def __init__(self, *args, **kwargs):
                raise RuntimeError()

        # `setitem` registers the fake model and reverts it once the test is done,
        #  even if an assertion below fails, so the global `hardware_models` registry
        #  isn't polluted for the other tests.
        monkeypatch.setitem(hardware_models, "CrashingCamera", CrashingCamera)

        crashing_uid = "crashing_uid"
        ecosystem.config.hardware_dict[crashing_uid] = {
            "name": "crashing_device",
            "active": True,
            "address": "PICAMERA",
            "type": gv.HardwareType.camera,
            "level": gv.HardwareLevel.environment,
            "model": "CrashingCamera",
        }

        # Make sure the crashing hardware is needed but not mounted yet
        assert crashing_uid in ecosystem.get_hardware_needed()
        assert crashing_uid not in ecosystem.hardware
        # Remove a healthy hardware
        await ecosystem.remove_hardware(test_data.sensor_uid)
        assert test_data.sensor_uid not in ecosystem.hardware
        # This shouldn't raise
        await ecosystem.refresh_hardware()
        # The crashing hardware has been flagged as failing ...
        assert crashing_uid not in ecosystem.get_hardware_needed()
        assert crashing_uid in ecosystem._failing_hardware
        assert crashing_uid not in ecosystem.hardware
        # ... while the other (healthy) hardware has been mounted
        assert test_data.sensor_uid in ecosystem.hardware

    async def test_actuators_data(self, ecosystem: Ecosystem):
        actuator_states = ecosystem.actuator_hub.as_dict()
        assert len(actuator_states) == len(default_actuators.climate_to_group_mapping)
        for actuator in actuator_states.values():
            assert not actuator["active"]
            assert not actuator["status"]
            assert actuator["mode"] is gv.ActuatorMode.automatic

    async def test_turn_actuator(self, ecosystem: Ecosystem):
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
