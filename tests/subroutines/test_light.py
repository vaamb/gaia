from datetime import time

import pytest

import gaia_validators as gv

from gaia import Ecosystem
from gaia.subroutines import Light, Sensors

from ..data import light_uid


@pytest.mark.asyncio
async def test_manageable(ecosystem: Ecosystem, light_subroutine: Light):
    assert light_subroutine.manageable

    ecosystem.config.delete_hardware(light_uid)
    await ecosystem.refresh_hardware()

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
    light_subroutine.config._lighting_method = gv.LightMethod.elongate
    assert not light_subroutine._compute_target_status(now)
    light_subroutine.config._lighting_method = gv.LightMethod.fixed
    assert not light_subroutine._compute_target_status(now)

    now = time(9)
    light_subroutine.config.lighting_hours = lighting_hours
    light_subroutine.config._lighting_method = gv.LightMethod.elongate
    assert light_subroutine._compute_target_status(now)
    light_subroutine.config._lighting_method = gv.LightMethod.fixed
    assert light_subroutine._compute_target_status(now)

    now = time(11)
    light_subroutine.config.lighting_hours = lighting_hours
    light_subroutine.config._lighting_method = gv.LightMethod.elongate
    assert not light_subroutine._compute_target_status(now)
    light_subroutine.config._lighting_method = gv.LightMethod.fixed
    assert light_subroutine._compute_target_status(now)

    now = time(21)
    light_subroutine.config.lighting_hours = lighting_hours
    light_subroutine.config._lighting_method = gv.LightMethod.elongate
    assert not light_subroutine._compute_target_status(now)
    light_subroutine.config._lighting_method = gv.LightMethod.fixed
    assert not light_subroutine._compute_target_status(now)


def test_hardware_needed(light_subroutine: Light):
    uids = light_subroutine.get_hardware_needed_uid()
    assert uids == {light_uid}


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
    # Sensors data are required for full capabilities
    sensors_subroutine.enable()
    await sensors_subroutine.start()

    # Enable the subroutines
    light_subroutine.enable()

    # Test start, routine, refresh and stop
    await light_subroutine.start()

    assert light_subroutine.actuator_handler.group == "light"
    assert len(light_subroutine.light_sensors) > 0

    await light_subroutine.routine()

    await light_subroutine.refresh()

    await light_subroutine.stop()

    # Disable the subroutine
    light_subroutine.disable()

    # Stop the sensors subroutine and disable it
    await sensors_subroutine.stop()
    sensors_subroutine.disable()
