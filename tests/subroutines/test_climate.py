from datetime import time
from typing import cast

import pytest

import gaia_validators as gv

from gaia import Ecosystem
from gaia.subroutines.sensors import Sensors
from gaia.subroutines.climate import Climate

from tests import data as test_data


climate_dict = {
    test_data.sensor_uid: test_data.sensor_info,  # DHT22 for temperature and humidity measures
    test_data.heater_uid: test_data.heater_info,
    test_data.humidifier_uid: test_data.humidifier_info,
    test_data.ws_dimmer_uid: test_data.ws_dimmer_info,  # dehumidifier
}


@pytest.mark.asyncio
@pytest.mark.parametrize("ecosystem", [{"hardware": climate_dict}], indirect=True)
class TestClimateSubroutine:
    async def test_manageable(self, ecosystem: Ecosystem, climate_subroutine: Climate):
        # Save config
        climate_config = ecosystem.config.climate

        assert climate_subroutine.manageable

        # Make sure sensors subroutine is required
        await climate_subroutine.ecosystem.stop_subroutine("sensors")
        assert not climate_subroutine.manageable

        await climate_subroutine.ecosystem.start_subroutine("sensors")
        assert climate_subroutine.manageable

        # Make sure a climate parameter is needed
        ecosystem.config.environment["climate"] = {}
        assert not climate_subroutine.manageable

        climate_subroutine.ecosystem.config.set_climate_parameter(
            parameter="temperature", day=25, night=20, hysteresis=2)
        assert climate_subroutine.manageable

        # Make sure a regulator is needed
        climate_subroutine.ecosystem.config.delete_hardware(test_data.heater_uid)
        await ecosystem.refresh_hardware()
        assert not climate_subroutine.manageable

        # Restore config
        ecosystem.config.environment["climate"] = climate_config

    async def test_target(self, climate_subroutine: Climate):
        day = time(hour=12)
        target = climate_subroutine.compute_target(gv.ClimateParameter.temperature, day)
        assert target[0] == test_data.temperature_cfg["day"]
        assert target[1] == test_data.temperature_cfg["hysteresis"]

        night = time(hour=22)
        target = climate_subroutine.compute_target(gv.ClimateParameter.temperature, night)
        assert target[0] == test_data.temperature_cfg["night"]
        assert target[1] == test_data.temperature_cfg["hysteresis"]

    async def test_hardware_needed(self, climate_subroutine: Climate):
        uids = climate_subroutine.get_hardware_needed_uid()
        assert uids == {
            test_data.heater_uid,
            test_data.humidifier_uid,
            test_data.ws_dimmer_uid,
        }

    async def test_expected_actuators(self, climate_subroutine: Climate):
        expected_actuators = climate_subroutine.compute_expected_actuators()
        assert expected_actuators == {
            # Default actuator
            (gv.ClimateParameter.temperature, "increase"): "heater",
            (gv.ClimateParameter.humidity, "decrease"): "dehumidifier",
            # Overridden actuator
            (gv.ClimateParameter.humidity, "increase"): "fogger",
        }

    async def test_turn_actuator(self, climate_subroutine: Climate):
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

        with pytest.raises(ValueError, match=r"Actuator group 'cooler' is not mounted."):
            await climate_subroutine.turn_climate_actuator(invalid_actuator_group, gv.ActuatorModePayload.on)

        with pytest.raises(ValueError, match=r"Actuator group 'cooler' is not mounted."):
            await climate_subroutine.turn_climate_actuator(invalid_actuator_group, gv.ActuatorModePayload.off)

        with pytest.raises(ValueError):
            await climate_subroutine.turn_climate_actuator(
                "WrongHardwareType", gv.ActuatorModePayload.automatic)

        with pytest.raises(ValueError):
            await climate_subroutine.turn_climate_actuator(valid_actuator_group, "WrongMode")

        handler.deactivate()

    async def test_regulated_parameters(self, climate_subroutine: Climate):
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
        assert set(parameters) == {gv.ClimateParameter.temperature, gv.ClimateParameter.humidity}  # depends on hardware available

    async def test_safe_stop_from_sensors(
            self,
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

    async def test_get_measure_for_parameter(self, climate_subroutine: Climate):
        measure = climate_subroutine._get_measure_for_parameter(gv.ClimateParameter.temperature)
        assert measure == gv.ClimateParameter.temperature.name

        measure = climate_subroutine._get_measure_for_parameter(gv.ClimateParameter.humidity)
        assert measure == "absolute_humidity"

    async def test_routine(self, climate_subroutine: Climate, sensors_subroutine: Sensors):
        # Sensors data are required ...
        await sensors_subroutine.routine()
        assert not isinstance(sensors_subroutine.sensors_data, gv.Empty)

        # Enable the subroutine
        climate_subroutine.enable()

        await climate_subroutine.start()

        assert set(climate_subroutine.regulated_parameters) == {
            gv.ClimateParameter.temperature,
            gv.ClimateParameter.humidity,
        }

        assert set(climate_subroutine.actuator_handlers) == {
            (gv.ClimateParameter.temperature, "increase"),  # "heater"
            (gv.ClimateParameter.humidity, "decrease"),  # "dehumidifier"
            (gv.ClimateParameter.humidity, "increase"),  # "fogger"
        }

        assert set(climate_subroutine.pids) == {
            gv.ClimateParameter.temperature,
            gv.ClimateParameter.humidity,
        }

        await climate_subroutine.routine()

        await climate_subroutine.refresh()

        await climate_subroutine.stop()

        # Disable the subroutine
        climate_subroutine.disable()
