import pytest

import gaia_validators as gv

from gaia import EngineConfig
from gaia.subroutines import Sensors

from ..data import heater_info, heater_uid, sensor_info, sensor_uid
from ..utils import get_logs_content


def test_manageable(sensors_subroutine: Sensors):
    assert sensors_subroutine.manageable

    sensors_subroutine.ecosystem.config.delete_hardware(sensor_uid)

    assert not sensors_subroutine.manageable


def test_hardware_needed(sensors_subroutine: Sensors):
    uids = sensors_subroutine.get_hardware_needed_uid()
    assert uids == {sensor_uid}


def test_add_hardware(sensors_subroutine: Sensors, engine_config: EngineConfig):
    sensors_subroutine.add_hardware(gv.HardwareConfig(uid=sensor_uid, **sensor_info))

    sensors_subroutine.add_hardware(gv.HardwareConfig(uid=heater_uid, **heater_info))
    with get_logs_content(engine_config.logs_dir / "base.log") as logs:
        assert "not in the list of the hardware available." in logs


def test_update_sensors_data(sensors_subroutine: Sensors):
    # Rely on the correct implementation of virtualDHT22
    with pytest.raises(RuntimeError, match="Sensors subroutine has to be started"):
        sensors_subroutine.update_sensors_data()

    sensors_subroutine.enable()
    sensors_subroutine._started = True
    sensors_subroutine.refresh_hardware()

    assert sensors_subroutine.sensors_data == gv.Empty()

    sensors_subroutine.update_sensors_data()

    assert isinstance(sensors_subroutine.sensors_data, gv.SensorsData)

    sensors_subroutine._started = False
