import pytest

import gaia_validators as gv

from gaia import EngineConfig
from gaia.subroutines import Sensors

from ..data import heater_info, heater_uid, i2c_sensor_uid, sensor_info, sensor_uid
from ..utils import get_logs_content


def test_manageable(sensors_subroutine: Sensors):
    assert sensors_subroutine.manageable

    for hardware_uid in sensors_subroutine.get_hardware_needed_uid():
        sensors_subroutine.ecosystem.config.delete_hardware(hardware_uid)

    assert not sensors_subroutine.manageable


def test_hardware_needed(sensors_subroutine: Sensors):
    uids = sensors_subroutine.get_hardware_needed_uid()
    assert uids == {i2c_sensor_uid, sensor_uid}


@pytest.mark.asyncio
async def test_add_hardware(sensors_subroutine: Sensors, engine_config: EngineConfig):
    await sensors_subroutine.add_hardware(gv.HardwareConfig(uid=sensor_uid, **sensor_info))

    await sensors_subroutine.add_hardware(gv.HardwareConfig(uid=heater_uid, **heater_info))
    with get_logs_content(engine_config.logs_dir / "gaia.log") as logs:
        assert "not in the list of the hardware available." in logs


@pytest.mark.asyncio
async def test_update_sensors_data(sensors_subroutine: Sensors):
    # Rely on the correct implementation of virtualDHT22
    with pytest.raises(RuntimeError, match="Sensors subroutine has to be started"):
        await sensors_subroutine.update_sensors_data()

    sensors_subroutine.enable()
    sensors_subroutine._started = True
    await sensors_subroutine.refresh_hardware()

    assert sensors_subroutine.sensors_data == gv.Empty()

    await sensors_subroutine.update_sensors_data()

    assert isinstance(sensors_subroutine.sensors_data, gv.SensorsData)

    sensors_subroutine._started = False
