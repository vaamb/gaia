from datetime import time

import pytest

import gaia_validators as gv

from gaia import Ecosystem
from gaia.subroutines import Light, Sensors

from tests import data as test_data


light_dict = {
    test_data.i2c_sensor_veml7700_uid: test_data.i2c_sensor_veml7700_info,
    test_data.light_uid: test_data.light_info,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("ecosystem", [{"hardware": light_dict}], indirect=True)
class TestLightSubroutine:
    async def test_manageable(self, ecosystem: Ecosystem, light_subroutine: Light):
        assert light_subroutine.manageable

        ecosystem.config.delete_hardware(test_data.light_uid)
        await ecosystem.refresh_hardware()

        assert not light_subroutine.manageable

    async def test_expected_status(self, light_subroutine: Light):
        lighting_hours = gv.LightingHours(
            morning_start=time(8),
            morning_end=time(10),
            evening_start=time(18),
            evening_end=time(20),
        )

        now = time(6)
        light_subroutine.config._lighting_hours = lighting_hours
        light_subroutine.config._lighting_method = gv.LightMethod.elongate
        assert not light_subroutine._compute_target_status(now)
        light_subroutine.config._lighting_method = gv.LightMethod.fixed
        assert not light_subroutine._compute_target_status(now)

        now = time(9)
        light_subroutine.config._lighting_hours = lighting_hours
        light_subroutine.config._lighting_method = gv.LightMethod.elongate
        assert light_subroutine._compute_target_status(now)
        light_subroutine.config._lighting_method = gv.LightMethod.fixed
        assert light_subroutine._compute_target_status(now)

        now = time(11)
        light_subroutine.config._lighting_hours = lighting_hours
        light_subroutine.config._lighting_method = gv.LightMethod.elongate
        assert not light_subroutine._compute_target_status(now)
        light_subroutine.config._lighting_method = gv.LightMethod.fixed
        assert light_subroutine._compute_target_status(now)

        now = time(21)
        light_subroutine.config._lighting_hours = lighting_hours
        light_subroutine.config._lighting_method = gv.LightMethod.elongate
        assert not light_subroutine._compute_target_status(now)
        light_subroutine.config._lighting_method = gv.LightMethod.fixed
        assert not light_subroutine._compute_target_status(now)

    async def test_hardware_needed(self, light_subroutine: Light):
        uids = light_subroutine.get_hardware_needed_uid()
        assert uids == {test_data.light_uid}

    async def test_turn_light(self, light_subroutine: Light):
        with pytest.raises(RuntimeError, match=r"Light subroutine is not started"):
            await light_subroutine.turn_light(gv.ActuatorModePayload.on)

        light_subroutine.enable()
        await light_subroutine.start()
        await light_subroutine.turn_light(gv.ActuatorModePayload.on)
        await light_subroutine.turn_light(gv.ActuatorModePayload.off)
        await light_subroutine.turn_light(gv.ActuatorModePayload.automatic)

        with pytest.raises(ValueError):
            await light_subroutine.turn_light("WrongMode")

    async def test_routine(self, sensors_subroutine: Sensors, light_subroutine: Light):
        # Sensors subroutine is required for full routine
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
