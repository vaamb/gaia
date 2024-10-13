import pytest

import gaia_validators as gv

from gaia import Ecosystem, EngineConfig
from gaia.exceptions import HardwareNotFound
from gaia.hardware.sensors.virtual import virtualDHT22

from ..data import sensor_info, sensor_uid
from ..utils import get_logs_content
from .dummy_subroutine import Dummy


hardware_info = sensor_info
hardware_uid = sensor_uid


@pytest.mark.asyncio
async def test_states(dummy_subroutine: Dummy, engine_config: EngineConfig):
    dummy_subroutine.manageable_state = False

    with pytest.raises(RuntimeError, match=r"The subroutine is not enabled."):
        await dummy_subroutine.start()
    with pytest.raises(RuntimeError, match=r"The subroutine is not running."):
        await dummy_subroutine.stop()
    assert not dummy_subroutine.enabled

    dummy_subroutine.enable()
    assert dummy_subroutine.enabled
    with get_logs_content(engine_config.logs_dir / "gaia.log") as logs:
        assert "Enabling the subroutine." in logs
    with pytest.raises(RuntimeError, match=r"The subroutine is not manageable."):
        await dummy_subroutine.start()

    dummy_subroutine.manageable_state = True
    await dummy_subroutine.start()
    with get_logs_content(engine_config.logs_dir / "gaia.log") as logs:
        assert "Starting the subroutine." in logs
    with pytest.raises(RuntimeError, match=r"The subroutine is already running."):
        await dummy_subroutine.start()

    await dummy_subroutine.stop()
    with get_logs_content(engine_config.logs_dir / "gaia.log") as logs:
        assert "Stopping the subroutine." in logs

    dummy_subroutine.disable()
    with get_logs_content(engine_config.logs_dir / "gaia.log") as logs:
        assert "Disabling the subroutine." in logs
    assert not dummy_subroutine.enabled


def test_properties(dummy_subroutine: Dummy, ecosystem: Ecosystem):
    dummy_subroutine.manageable_state = False
    assert not dummy_subroutine.manageable
    dummy_subroutine.manageable_state = True
    assert dummy_subroutine.manageable

    assert dummy_subroutine.ecosystem.__dict__ is ecosystem.__dict__


@pytest.mark.asyncio
async def test_hardware(dummy_subroutine: Dummy, engine_config: EngineConfig):
    assert dummy_subroutine.hardware_choices == {}

    hardware_config = gv.HardwareConfig(uid=hardware_uid, **hardware_info)

    with pytest.raises(RuntimeError, match=r"No 'hardware_choices' available."):
        await dummy_subroutine.add_hardware(hardware_config)

    dummy_subroutine.hardware_choices = {virtualDHT22.__name__: virtualDHT22}

    await dummy_subroutine.add_hardware(hardware_config)
    with get_logs_content(engine_config.logs_dir / "gaia.log") as logs:
        assert f"Hardware {hardware_config.name} has been set up." in logs

    await dummy_subroutine.remove_hardware(hardware_uid)
    with get_logs_content(engine_config.logs_dir / "gaia.log") as logs:
        assert f"Hardware {hardware_config.name} has been dismounted." in logs

    with pytest.raises(HardwareNotFound, match=f"Hardware '{hardware_uid}' not found."):
        await dummy_subroutine.remove_hardware(hardware_uid)


@pytest.mark.asyncio
async def test_subroutine(dummy_subroutine: Dummy):
    with pytest.raises(RuntimeError, match=r"subroutine has to be started"):
        await dummy_subroutine.routine()

    dummy_subroutine.enable()
    await dummy_subroutine.start()

    await dummy_subroutine.routine()

    await dummy_subroutine.stop()
    dummy_subroutine.disable()
