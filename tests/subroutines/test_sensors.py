import pytest

import gaia_validators as gv

from gaia import Ecosystem
from gaia.subroutines import Sensors

from tests import data as test_data


sensors_dict = {test_data.sensor_uid: test_data.sensor_info}


@pytest.mark.asyncio
@pytest.mark.parametrize("ecosystem", [{"hardware": sensors_dict}], indirect=True)
class TestSensorsSubroutine:
    async def test_manageable(self, ecosystem: Ecosystem, sensors_subroutine: Sensors):
        assert sensors_subroutine.manageable

        for hardware_uid in sensors_subroutine.get_hardware_needed_uid():
            ecosystem.config.delete_hardware(hardware_uid)

        await ecosystem.refresh_hardware()

        assert not sensors_subroutine.manageable

    async def test_hardware_needed(self, sensors_subroutine: Sensors):
        uids = sensors_subroutine.get_hardware_needed_uid()
        assert uids == {test_data.sensor_uid, }

    async def test_routine(self, sensors_subroutine: Sensors):
        # Rely on the correct implementation of virtualDHT22
        # Setup climate parameters to test alarms
        sensors_subroutine.config.set_management(gv.ManagementFlags.alarms, True)
        sensors_subroutine.config.set_climate_parameter(
            "temperature", **{"day": 42.0, "night": 42.0, "hysteresis": 1.0, "alarm": 0.5})

        # Enable the subroutine
        sensors_subroutine.enable()

        # Test start, routine, refresh and stop
        await sensors_subroutine.start()

        assert sensors_subroutine.sensors_data == gv.Empty()

        await sensors_subroutine.routine()

        assert isinstance(sensors_subroutine.sensors_data, gv.SensorsData)
        assert len(sensors_subroutine.sensors_data.records) > 0
        assert len(sensors_subroutine.sensors_data.average) > 0
        assert len(sensors_subroutine.sensors_data.alarms) > 0

        assert sensors_subroutine.ecosystem.sensors_data.records

        await sensors_subroutine.refresh()

        await sensors_subroutine.stop()

        sensors_subroutine.disable()
