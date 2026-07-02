import pytest

from gaia import Ecosystem
from gaia.subroutines import Pictures

import tests.data as test_data


pictures_dict = {
    test_data.camera_uid: test_data.camera_info,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("ecosystem", [{"hardware": pictures_dict}], indirect=True)
class TestPictureSubroutine:
    async def test_manageable(
            self,
            ecosystem: Ecosystem,
            pictures_subroutine: Pictures,
    ):
        assert pictures_subroutine.manageable

        ecosystem.config.delete_hardware(test_data.camera_uid)
        await ecosystem.refresh_hardware()

        assert not pictures_subroutine.manageable

    async def test_hardware_needed(self, pictures_subroutine: Pictures):
        uids = pictures_subroutine.get_hardware_needed_uid()
        assert uids == {test_data.camera_uid}

    async def test_routine(self, pictures_subroutine: Pictures):
        # Enable the subroutine
        pictures_subroutine.enable()

        # Test start, routine, refresh and stop
        await pictures_subroutine.start()

        assert not pictures_subroutine.picture_arrays

        await pictures_subroutine.routine()

        assert pictures_subroutine.picture_arrays

        await pictures_subroutine.refresh()

        await pictures_subroutine.stop()

        # Disable the subroutine
        pictures_subroutine.disable()

    async def test_reset_background_arrays(self, pictures_subroutine: Pictures):
        pictures_subroutine.enable()
        await pictures_subroutine.start()

        assert pictures_subroutine.ecosystem.picture_arrays

        await pictures_subroutine.reset_background_arrays()
