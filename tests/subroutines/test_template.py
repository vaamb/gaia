from unittest.mock import patch

import pytest

from gaia import Ecosystem
from gaia.subroutines import subroutine_dict, subroutine_names

from ..data import sensor_info, sensor_uid
from .dummy_subroutine import Dummy


hardware_info = sensor_info
hardware_uid = sensor_uid


@pytest.mark.asyncio
class TestSubroutineTemplate:
    async def test_subroutine_dict_sync(self):
        # Ensure `subroutine_dict` key and values stay consistent
        for subroutine_name, subroutine in subroutine_dict.items():
            assert subroutine_name == subroutine.__name__.lower()
        # Ensure `subroutine_names` and `subroutine_dict` keys remain identical
        assert sorted(subroutine_names) == sorted(subroutine_dict.keys())

    async def test_states(
            self,
            dummy_subroutine: Dummy,
            caplog: pytest.LogCaptureFixture,
    ):
        dummy_subroutine.manageable_state = False

        with pytest.raises(RuntimeError, match=r"The subroutine is not enabled."):
            await dummy_subroutine.start()
        with pytest.raises(RuntimeError, match=r"The subroutine is not running."):
            await dummy_subroutine.stop()
        assert not dummy_subroutine.enabled

        caplog.clear()
        dummy_subroutine.enable()
        assert dummy_subroutine.enabled
        assert "Enabling the subroutine." in caplog.messages
        with pytest.raises(RuntimeError, match=r"The subroutine is not manageable."):
            await dummy_subroutine.start()

        caplog.clear()
        dummy_subroutine.manageable_state = True
        await dummy_subroutine.start()
        assert "Starting the subroutine." in caplog.messages
        with pytest.raises(RuntimeError, match=r"The subroutine is already running."):
            await dummy_subroutine.start()

        caplog.clear()
        await dummy_subroutine.stop()
        assert "Stopping the subroutine." in caplog.messages

        caplog.clear()
        dummy_subroutine.disable()
        assert "Disabling the subroutine." in caplog.messages
        assert not dummy_subroutine.enabled

    async def test_start_and_stop_failures(
            self,
            dummy_subroutine: Dummy,
            caplog: pytest.LogCaptureFixture,
    ):
        dummy_subroutine.enable()

        # A failing `_start()` should be logged, re-raised and leave the
        # subroutine stopped
        caplog.clear()
        with patch.object(dummy_subroutine, "_start", side_effect=RuntimeError("Oops")):
            with pytest.raises(RuntimeError, match=r"Oops"):
                await dummy_subroutine.start()
        assert "Starting failed." in caplog.messages[2]
        assert not dummy_subroutine.started

        await dummy_subroutine.start()

        # A failing `_stop()` should be logged, re-raised and leave the
        # subroutine started
        caplog.clear()
        with patch.object(dummy_subroutine, "_stop", side_effect=RuntimeError("Oops")):
            with pytest.raises(RuntimeError, match=r"Oops"):
                await dummy_subroutine.stop()
        assert "Stopping failed." in caplog.messages[1]
        assert dummy_subroutine.started

        await dummy_subroutine.stop()
        dummy_subroutine.disable()

    async def test_properties(self, dummy_subroutine: Dummy, ecosystem: Ecosystem):
        dummy_subroutine.manageable_state = False
        assert not dummy_subroutine.manageable
        dummy_subroutine.manageable_state = True
        assert dummy_subroutine.manageable

        assert dummy_subroutine.ecosystem.__dict__ is ecosystem.__dict__

    async def test_subroutine(self, dummy_subroutine: Dummy):
        with pytest.raises(RuntimeError, match=r"subroutine has to be started"):
            await dummy_subroutine.routine()

        dummy_subroutine.enable()
        await dummy_subroutine.start()

        await dummy_subroutine.routine()

        await dummy_subroutine.stop()
        dummy_subroutine.disable()
