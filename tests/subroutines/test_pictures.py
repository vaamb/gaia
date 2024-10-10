import pytest

import gaia_validators as gv

from gaia import EngineConfig
from gaia.subroutines import Pictures

from ..data import heater_info, heater_uid, sensor_info, camera_uid
from ..utils import get_logs_content


def test_manageable(pictures_subroutine: Pictures):
    assert pictures_subroutine.manageable

    pictures_subroutine.ecosystem.config.delete_hardware(camera_uid)

    assert not pictures_subroutine.manageable


def test_hardware_needed(pictures_subroutine: Pictures):
    uids = pictures_subroutine.get_hardware_needed_uid()
    assert uids == {camera_uid}


@pytest.mark.asyncio
async def test_routine(pictures_subroutine: Pictures):
    with pytest.raises(RuntimeError, match="Pictures subroutine has to be started"):
        await pictures_subroutine.routine()

    pictures_subroutine.enable()
    pictures_subroutine._started = True
    await pictures_subroutine.refresh_hardware()

    assert not pictures_subroutine.picture_arrays

    await pictures_subroutine.routine()

    assert pictures_subroutine.picture_arrays

    pictures_subroutine._started = False
