from typing import cast

import pytest

import gaia_validators as gv

from gaia import Ecosystem
from gaia.subroutines.sensors import Sensors
from gaia.subroutines.climate import Climate

from ..data import heater_uid


@pytest.mark.asyncio
async def test_manageable(ecosystem: Ecosystem, climate_subroutine: Climate):
    assert climate_subroutine.manageable

    # Make sure sensors subroutine is required
    await climate_subroutine.ecosystem.stop_subroutine("sensors")
    assert not climate_subroutine.manageable

    await climate_subroutine.ecosystem.start_subroutine("sensors")
    assert climate_subroutine.manageable

    # Make sure a climate parameter is needed
    climate_subroutine.ecosystem.config.delete_climate_parameter("temperature")
    assert not climate_subroutine.manageable

    climate_subroutine.ecosystem.config.set_climate_parameter(
        parameter="temperature", day=25, night=20, hysteresis=2)
    assert climate_subroutine.manageable

    # Make sure a regulator is needed
    climate_subroutine.ecosystem.config.delete_hardware(heater_uid)
    await ecosystem.refresh_hardware()
    assert not climate_subroutine.manageable


def test_target(climate_subroutine: Climate):
    # TODO
    pass


def test_hardware_needed(climate_subroutine: Climate):
    climate_subroutine.compute_expected_actuators()
    uids = climate_subroutine.get_hardware_needed_uid()
    assert uids == {heater_uid}


@pytest.mark.asyncio
async def test_turn_actuator(climate_subroutine: Climate):
    valid_actuator_group: str = cast(str, gv.HardwareType.heater.name)
    invalid_actuator_group: str = cast(str, gv.HardwareType.cooler.name)

    with pytest.raises(RuntimeError, match=r"Climate subroutine is not started"):
        await climate_subroutine.turn_climate_actuator(valid_actuator_group, gv.ActuatorModePayload.on)

    climate_subroutine.enable()
    await climate_subroutine.start()
    handler = climate_subroutine.ecosystem.actuator_hub.get_handler(valid_actuator_group)
    handler.activate()

    await climate_subroutine.turn_climate_actuator(valid_actuator_group, gv.ActuatorModePayload.on)
    await climate_subroutine.turn_climate_actuator(valid_actuator_group, gv.ActuatorModePayload.off)
    await climate_subroutine.turn_climate_actuator(valid_actuator_group, gv.ActuatorModePayload.automatic)

    with pytest.raises(RuntimeError, match=r"This actuator is not active"):
        await climate_subroutine.turn_climate_actuator(invalid_actuator_group, gv.ActuatorModePayload.on)

    with pytest.raises(RuntimeError, match=r"This actuator is not active"):
        await climate_subroutine.turn_climate_actuator(invalid_actuator_group, gv.ActuatorModePayload.off)

    # TODO: currently, this raises KeyError because the direction cannot be found
    #  in the `actuator_to_direction` dict
    with pytest.raises(KeyError):
        await climate_subroutine.turn_climate_actuator(
            "WrongHardwareType", gv.ActuatorModePayload.automatic)

    with pytest.raises(ValueError):
        await climate_subroutine.turn_climate_actuator(valid_actuator_group, "WrongMode")


@pytest.mark.asyncio
async def test_regulated_parameters(climate_subroutine: Climate):
    parameters = climate_subroutine.regulated_parameters
    assert parameters == []

    # Computing manageable updates the regulated parameters but when not started
    #  it should still return an empty list
    assert climate_subroutine.manageable
    parameters = climate_subroutine.regulated_parameters
    assert parameters == []

    climate_subroutine.enable()
    await climate_subroutine.start()
    parameters = climate_subroutine.regulated_parameters
    assert parameters == ["temperature"]  # depends on hardware available


@pytest.mark.asyncio
async def test_safe_stop_from_sensors(
        climate_subroutine: Climate,
        sensors_subroutine: Sensors,
):
    assert sensors_subroutine.enabled
    assert sensors_subroutine.started

    climate_subroutine.enable()
    await climate_subroutine.start()

    assert climate_subroutine.enabled
    assert climate_subroutine.started

    await sensors_subroutine.stop()

    assert climate_subroutine.enabled
    assert not climate_subroutine.started
    assert not climate_subroutine.manageable


@pytest.mark.asyncio
async def test_routine(climate_subroutine: Climate, sensors_subroutine: Sensors):
    # Sensors data are required ...
    await sensors_subroutine.routine()
    assert not isinstance(sensors_subroutine.sensors_data, gv.Empty)

    climate_subroutine.enable()
    await climate_subroutine.start()

    climate_subroutine.compute_expected_actuators()

    await climate_subroutine.routine()

    assert climate_subroutine.ecosystem.climate_parameters_regulated()
