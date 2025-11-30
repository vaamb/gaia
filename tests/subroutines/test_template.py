import pytest

from gaia import Ecosystem, EngineConfig

from ..data import debug_log_file, sensor_info, sensor_uid
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
    with get_logs_content(engine_config.logs_dir / debug_log_file) as logs:
        assert "Enabling the subroutine." in logs
    with pytest.raises(RuntimeError, match=r"The subroutine is not manageable."):
        await dummy_subroutine.start()

    dummy_subroutine.manageable_state = True
    await dummy_subroutine.start()
    with get_logs_content(engine_config.logs_dir / debug_log_file) as logs:
        assert "Starting the subroutine." in logs
    with pytest.raises(RuntimeError, match=r"The subroutine is already running."):
        await dummy_subroutine.start()

    await dummy_subroutine.stop()
    with get_logs_content(engine_config.logs_dir / debug_log_file) as logs:
        assert "Stopping the subroutine." in logs

    dummy_subroutine.disable()
    with get_logs_content(engine_config.logs_dir / debug_log_file) as logs:
        assert "Disabling the subroutine." in logs
    assert not dummy_subroutine.enabled


def test_properties(dummy_subroutine: Dummy, ecosystem: Ecosystem):
    dummy_subroutine.manageable_state = False
    assert not dummy_subroutine.manageable
    dummy_subroutine.manageable_state = True
    assert dummy_subroutine.manageable

    assert dummy_subroutine.ecosystem.__dict__ is ecosystem.__dict__


@pytest.mark.asyncio
async def test_subroutine(dummy_subroutine: Dummy):
    with pytest.raises(RuntimeError, match=r"subroutine has to be started"):
        await dummy_subroutine.routine()

    dummy_subroutine.enable()
    await dummy_subroutine.start()

    await dummy_subroutine.routine()

    await dummy_subroutine.stop()
    dummy_subroutine.disable()
