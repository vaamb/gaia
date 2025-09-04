import pytest

import gaia_validators as gv

from gaia import Ecosystem
from gaia.subroutines import Sensors

from ..data import i2c_sensor_ens160_uid, i2c_sensor_veml7700_uid, sensor_uid


@pytest.mark.asyncio
async def test_manageable(ecosystem: Ecosystem, sensors_subroutine: Sensors):
    assert sensors_subroutine.manageable

    for hardware_uid in sensors_subroutine.get_hardware_needed_uid():
        ecosystem.config.delete_hardware(hardware_uid)

    await ecosystem.refresh_hardware()

    assert not sensors_subroutine.manageable


def test_hardware_needed(sensors_subroutine: Sensors):
    uids = sensors_subroutine.get_hardware_needed_uid()
    assert uids == {i2c_sensor_ens160_uid, i2c_sensor_veml7700_uid, sensor_uid}


@pytest.mark.asyncio
async def test_routine(sensors_subroutine: Sensors):
    # Rely on the correct implementation of virtualDHT22

    sensors_subroutine.config.set_management(gv.ManagementFlags.alarms, True)
    sensors_subroutine.config.set_climate_parameter(
        "temperature", **{"day": 42.0, "night": 42.0, "hysteresis": 1.0, "alarm": 0.5})
    sensors_subroutine.enable()
    await sensors_subroutine.start()

    assert sensors_subroutine.sensors_data == gv.Empty()

    await sensors_subroutine.routine()

    assert isinstance(sensors_subroutine.sensors_data, gv.SensorsData)
    assert len(sensors_subroutine.sensors_data.records) > 0
    assert len(sensors_subroutine.sensors_data.average) > 0
    assert len(sensors_subroutine.sensors_data.alarms) > 0

    assert sensors_subroutine.ecosystem.sensors_data.records
