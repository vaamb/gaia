import pytest

from gaia.subroutines import Pictures

from ..data import camera_uid


def test_manageable(pictures_subroutine: Pictures):
    assert pictures_subroutine.manageable

    pictures_subroutine.ecosystem.config.delete_hardware(camera_uid)

    assert not pictures_subroutine.manageable


def test_hardware_needed(pictures_subroutine: Pictures):
    uids = pictures_subroutine.get_hardware_needed_uid()
    assert uids == {camera_uid}


@pytest.mark.asyncio
async def test_routine(pictures_subroutine: Pictures):
    pictures_subroutine.config.set_management("camera", True)
    pictures_subroutine.enable()
    await pictures_subroutine.start()

    assert not pictures_subroutine.picture_arrays

    await pictures_subroutine.routine()

    assert pictures_subroutine.picture_arrays


@pytest.mark.asyncio
async def test_reset_background_arrays(pictures_subroutine: Pictures):
    pictures_subroutine.config.set_management("camera", True)
    pictures_subroutine.enable()
    await pictures_subroutine.start()

    await pictures_subroutine.reset_background_arrays()
