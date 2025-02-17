from datetime import time

import pytest

import gaia_validators as gv

from gaia import EngineConfig
from gaia.subroutines import Light, Sensors

from ..data import light_info, light_uid, sensor_info, sensor_uid
from ..utils import get_logs_content


def test_manageable(light_subroutine: Light):
    assert light_subroutine.manageable

    light_subroutine.ecosystem.config.delete_hardware(light_uid)

    assert not light_subroutine.manageable


def test_expected_status(light_subroutine: Light):
    lighting_hours = gv.LightingHours(
        morning_start=time(8),
        morning_end=time(10),
        evening_start=time(18),
        evening_end=time(20),
    )

    now = time(6)
    light_subroutine.config.lighting_hours = lighting_hours
    light_subroutine.config.lighting_method = gv.LightMethod.elongate
    assert not light_subroutine._compute_target_status(now)
    light_subroutine.config.lighting_method = gv.LightMethod.fixed
    assert not light_subroutine._compute_target_status(now)

    now = time(9)
    light_subroutine.config.lighting_hours = lighting_hours
    light_subroutine.config.lighting_method = gv.LightMethod.elongate
    assert light_subroutine._compute_target_status(now)
    light_subroutine.config.lighting_method = gv.LightMethod.fixed
    assert light_subroutine._compute_target_status(now)

    now = time(11)
    light_subroutine.config.lighting_hours = lighting_hours
    light_subroutine.config.lighting_method = gv.LightMethod.elongate
    assert not light_subroutine._compute_target_status(now)
    light_subroutine.config.lighting_method = gv.LightMethod.fixed
    assert light_subroutine._compute_target_status(now)

    now = time(21)
    light_subroutine.config.lighting_hours = lighting_hours
    light_subroutine.config.lighting_method = gv.LightMethod.elongate
    assert not light_subroutine._compute_target_status(now)
    light_subroutine.config.lighting_method = gv.LightMethod.fixed
    assert not light_subroutine._compute_target_status(now)


def test_hardware_needed(light_subroutine: Light):
    uids = light_subroutine.get_hardware_needed_uid()
    assert uids == {light_uid}


@pytest.mark.asyncio
async def test_add_hardware(light_subroutine: Light, engine_config: EngineConfig):
    await light_subroutine.add_hardware(gv.HardwareConfig(uid=light_uid, **light_info))

    await light_subroutine.add_hardware(
        gv.HardwareConfig(uid=sensor_uid, **sensor_info))
    with get_logs_content(engine_config.logs_dir / "gaia.log") as logs:
        assert "not in the list of the hardware available." in logs


@pytest.mark.asyncio
async def test_turn_light(light_subroutine: Light):
    with pytest.raises(RuntimeError, match=r"Light subroutine is not started"):
        await light_subroutine.turn_light(gv.ActuatorModePayload.on)

    light_subroutine.enable()
    await light_subroutine.start()
    await light_subroutine.turn_light(gv.ActuatorModePayload.on)
    await light_subroutine.turn_light(gv.ActuatorModePayload.off)
    await light_subroutine.turn_light(gv.ActuatorModePayload.automatic)

    with pytest.raises(ValueError):
        await light_subroutine.turn_light("WrongMode")


@pytest.mark.asyncio
async def test_routine(light_subroutine: Light, sensors_subroutine: Sensors):
    sensors_subroutine.enable()
    await sensors_subroutine.start()

    light_subroutine.enable()
    await light_subroutine.start()

    await light_subroutine.routine()

    await sensors_subroutine.stop()
    sensors_subroutine.disable()
