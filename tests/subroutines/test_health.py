import pytest

import gaia_validators as gv

from gaia.hardware.abc import Measure
from gaia.subroutines import Health, Light

from ..data import camera_uid
from ..utils import get_logs_content


def test_manageable(health_subroutine: Health):
    camera_cfg = health_subroutine.config.IO_dict[camera_uid].copy()
    assert health_subroutine.manageable

    health_subroutine.config.IO_dict[camera_uid]["measures"] = []
    assert not health_subroutine.manageable

    health_subroutine.config.IO_dict[camera_uid] = camera_cfg
    assert health_subroutine.manageable

    health_subroutine.config.delete_hardware(camera_uid)
    assert not health_subroutine.manageable


def test_hardware_needed(health_subroutine: Health):
    uids = health_subroutine.get_hardware_needed_uid()
    assert uids == {camera_uid}


@pytest.mark.asyncio
async def test_routine(health_subroutine: Health):
    health_subroutine.enable()
    await health_subroutine.start()

    assert health_subroutine.plants_health == gv.Empty()

    await health_subroutine.routine()

    await health_subroutine.stop()

    assert health_subroutine.plants_health != gv.Empty()
    assert isinstance(health_subroutine.plants_health["records"][0], gv.HealthRecord)


@pytest.mark.asyncio
async def test_light_switching(health_subroutine: Health, light_subroutine: Light):
    log_dir = health_subroutine.ecosystem.engine.config.logs_dir / "gaia.log"

    light_subroutine.enable()
    await light_subroutine.start()

    health_subroutine.enable()
    await health_subroutine.start()

    with get_logs_content(log_dir):
        pass  # Clear logs

    await health_subroutine._get_the_images()

    with get_logs_content(log_dir) as logs:
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
