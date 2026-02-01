import pytest

import gaia_validators as gv

from gaia import Ecosystem
from gaia.hardware.abc import Measure
from gaia.subroutines import Health, Light

from ..data import camera_uid


@pytest.mark.asyncio
async def test_manageable(ecosystem: Ecosystem, health_subroutine: Health):
    camera_cfg = health_subroutine.config.IO_dict[camera_uid].copy()
    assert health_subroutine.manageable

    health_subroutine.config.IO_dict[camera_uid]["measures"] = []
    await ecosystem.refresh_hardware()
    assert not health_subroutine.manageable

    health_subroutine.config.IO_dict[camera_uid] = camera_cfg
    await ecosystem.refresh_hardware()
    assert health_subroutine.manageable

    health_subroutine.config.delete_hardware(camera_uid)
    await ecosystem.refresh_hardware()
    assert not health_subroutine.manageable


def test_hardware_needed(health_subroutine: Health):
    uids = health_subroutine.get_hardware_needed_uid()
    assert uids == {camera_uid}


@pytest.mark.asyncio
async def test_routine(health_subroutine: Health):
    # Enable the subroutine
    health_subroutine.enable()

    # Test start, routine, refresh and stop
    await health_subroutine.start()

    assert health_subroutine.plants_health == gv.Empty()

    await health_subroutine.routine()

    assert health_subroutine.plants_health != gv.Empty()
    assert isinstance(health_subroutine.plants_health["records"][0], gv.HealthRecord)

    await health_subroutine.refresh()

    await health_subroutine.stop()

    # Disable the subroutine
    health_subroutine.disable()


@pytest.mark.asyncio
async def test_light_switching(health_subroutine: Health, light_subroutine: Light, logs_content):
    light_subroutine.enable()
    await light_subroutine.start()

    health_subroutine.enable()
    await health_subroutine.start()

    with logs_content():
        pass  # Clear logs

    await health_subroutine._get_the_images()

    with logs_content() as logs:
        assert "Light has been set to 'manual' mode" in logs
        assert "Light has been set to 'automatic' mode" in logs

    await light_subroutine.stop()
    light_subroutine.disable()


@pytest.mark.asyncio
async def test_index(health_subroutine: Health):
    health_subroutine.enable()
    await health_subroutine.start()

    camera = health_subroutine.hardware[camera_uid]

    image = await camera.get_image()
    index = health_subroutine._get_index(image, Measure.mpri)
    assert isinstance(index, float)
