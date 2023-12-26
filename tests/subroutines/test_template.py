import pytest

import gaia_validators as gv

from gaia import Ecosystem, EngineConfig
from gaia.exceptions import HardwareNotFound
from gaia.hardware.actuators.GPIO import gpioSwitch
from gaia.subroutines.dummy import Dummy

from ..utils import get_logs_content
from ..data import hardware_info, hardware_uid


def test_states(dummy_subroutine: Dummy, engine_config: EngineConfig):
    dummy_subroutine.manageable_state = False

    with pytest.raises(RuntimeError, match=r"The subroutine is not enabled."):
        dummy_subroutine.start()
    with pytest.raises(RuntimeError, match=r"The subroutine is not running."):
        dummy_subroutine.stop()
    assert not dummy_subroutine.enabled

    dummy_subroutine.enable()
    assert dummy_subroutine.enabled
    with get_logs_content(engine_config.logs_dir / "base.log") as logs:
        assert "Enabling the subroutine." in logs
    with pytest.raises(RuntimeError, match=r"The subroutine is not manageable."):
        dummy_subroutine.start()

    dummy_subroutine.manageable_state = True
    dummy_subroutine.start()
    with get_logs_content(engine_config.logs_dir / "base.log") as logs:
        assert "Starting the subroutine." in logs
    with pytest.raises(RuntimeError, match=r"The subroutine is already running."):
        dummy_subroutine.start()

    dummy_subroutine.stop()
    with get_logs_content(engine_config.logs_dir / "base.log") as logs:
        assert "Stopping the subroutine." in logs

    dummy_subroutine.disable()
    with get_logs_content(engine_config.logs_dir / "base.log") as logs:
        assert "Disabling the subroutine." in logs
    assert not dummy_subroutine.enabled


def test_properties(dummy_subroutine: Dummy, ecosystem: Ecosystem):
    dummy_subroutine.manageable_state = False
    assert not dummy_subroutine.manageable
    dummy_subroutine.manageable_state = True
    assert dummy_subroutine.manageable

    assert dummy_subroutine.ecosystem.__dict__ is ecosystem.__dict__


def test_hardware(dummy_subroutine: Dummy, engine_config: EngineConfig):
    assert dummy_subroutine.hardware_choices == {}

    hardware_config = gv.HardwareConfig(uid=hardware_uid, **hardware_info)

    with pytest.raises(RuntimeError, match=r"No 'hardware_choices' available."):
        dummy_subroutine.add_hardware(hardware_config)

    dummy_subroutine.hardware_choices = {gpioSwitch.__name__: gpioSwitch}

    dummy_subroutine.add_hardware(hardware_config)
    with get_logs_content(engine_config.logs_dir / "base.log") as logs:
        assert f"Hardware {hardware_config.name} has been set up." in logs

    dummy_subroutine.remove_hardware(hardware_uid)
    with get_logs_content(engine_config.logs_dir / "base.log") as logs:
        assert f"Hardware {hardware_config.name} has been dismounted." in logs

    with pytest.raises(HardwareNotFound, match=f"Hardware '{hardware_uid}' not found."):
        dummy_subroutine.remove_hardware(hardware_uid)
