import asyncio

import pytest

import gaia_validators as gv

from gaia import Ecosystem
from gaia.actuator_handler import Timer
from gaia.subroutines.weather import Weather

from tests import data as test_data
from tests.utils import with_hardware


weather_dict = {
    test_data.humidifier_uid: test_data.humidifier_info,
}


@pytest.mark.asyncio
@with_hardware(weather_dict)
class TestWeatherSubroutine:
    async def test_manageable(self, ecosystem: Ecosystem, weather_subroutine: Weather):
        assert weather_subroutine.manageable

        # Make sure a weather parameter is needed
        weather_config = ecosystem.config.weather

        ecosystem.config.environment["weather"] = {}
        assert not weather_subroutine.manageable

        ecosystem.config.environment["weather"] = weather_config
        assert weather_subroutine.manageable

        # Make sure a hardware belonging to the group is needed
        ecosystem.config.delete_hardware(test_data.humidifier_uid)
        await ecosystem.refresh_hardware()
        assert not weather_subroutine.manageable

    async def test_hardware_needed(self, weather_subroutine: Weather):
        uids = weather_subroutine.get_hardware_needed_uid()
        assert uids == {test_data.humidifier_uid}

    async def test_routine(self, weather_subroutine: Weather):
        # Enable the subroutine
        weather_subroutine.enable()

        assert not weather_subroutine._active_parameters
        assert not weather_subroutine._jobs

        # Test start, refresh and stop (weather has no routine)
        await weather_subroutine.start()

        assert weather_subroutine._active_parameters == {"rain": "rainer"}
        assert weather_subroutine._jobs
        actuator_handler = weather_subroutine.get_actuator_handler("rainer")
        assert actuator_handler.active
        active_refs = actuator_handler._active

        with pytest.raises(ValueError):
            await weather_subroutine.routine()

        await weather_subroutine.refresh()

        await weather_subroutine.stop()

        # Stopping should deactivate the actuator handlers it activated
        assert not weather_subroutine._active_parameters
        # Only the reference taken by `start()` is released
        assert actuator_handler._active == active_refs - 1

        # Disable the subroutine
        weather_subroutine.disable()

    async def test_activate_actuator_handler(self, weather_subroutine: Weather):
        # Test activating the actuator handler
        await weather_subroutine._activate_actuator_handler("rain")

        # Rainer is the actuator group for the rain parameter
        actuator_group = weather_subroutine.get_actuator_group_for_parameter("rain")
        assert weather_subroutine._active_parameters == {"rain": actuator_group}
        actuator_handler = weather_subroutine.get_actuator_handler(actuator_group)
        assert actuator_handler.active

        # Should raise if the actuator handler is already activated
        with pytest.raises(ValueError):
            await weather_subroutine._activate_actuator_handler("rain")

        # Test deactivating the actuator handler
        await weather_subroutine._inactivate_actuator_handler("rain")

        assert "rain" not in weather_subroutine._active_parameters
        assert not actuator_handler.active

        # Should raise if the actuator handler is not activated
        with pytest.raises(ValueError):
            await weather_subroutine._inactivate_actuator_handler("rain")

    async def test_refresh_relink_parameter(
            self,
            ecosystem: Ecosystem,
            weather_subroutine: Weather,
    ):
        weather_subroutine.enable()
        await weather_subroutine.start()

        # "rainer" is defined as "rain"'s linked actuator
        assert weather_subroutine._active_parameters == {"rain": "rainer"}
        rainer_handler = weather_subroutine.get_actuator_handler("rainer")
        assert rainer_handler.active

        # Link "rain" to another group. Refreshing should deactivate "rainer" and
        #  activate "fogger" instead
        ecosystem.config.update_weather_parameter("rain", linked_actuator="fogger")
        await weather_subroutine.refresh()

        assert weather_subroutine._active_parameters == {"rain": "fogger"}
        assert not rainer_handler.active
        fogger_handler = weather_subroutine.get_actuator_handler("fogger")
        assert fogger_handler.active

        # Delete "rain" altogether, it shouldn't impact anything
        ecosystem.config.delete_weather_parameter("rain")
        await weather_subroutine.stop()

        assert not weather_subroutine._active_parameters
        assert not fogger_handler.active

        weather_subroutine.disable()

    async def test_default_actuator_group(
            self,
            ecosystem: Ecosystem,
            weather_subroutine: Weather,
    ):
        # Without a linked actuator, the default actuator group ("rainer"), not
        #  the name of the weather parameter ("rain"), should be used
        ecosystem.config.update_weather_parameter("rain", linked_actuator=None)

        assert weather_subroutine.compute_expected_actuators() == {"rain": "rainer"}
        assert weather_subroutine.get_actuator_group_for_parameter("rain") == "rainer"

        await weather_subroutine._activate_actuator_handler("rain")
        assert weather_subroutine._active_parameters["rain"] == "rainer"

        await weather_subroutine._inactivate_actuator_handler("rain")

    async def test_parameter_without_actuator_group(
            self,
            ecosystem: Ecosystem,
            weather_subroutine: Weather,
            caplog: pytest.LogCaptureFixture,
    ):
        # A free-form weather parameter has no default actuator group: without
        #  a linked actuator it is skipped rather than failing the subroutine
        ecosystem.config.set_weather_parameter(
            "storm", pattern="0 * * * *", duration=10, level=100.0)

        with caplog.at_level("DEBUG"):
            assert weather_subroutine.compute_expected_actuators() == {"rain": "rainer"}
        assert "'storm' has no linked actuator" in caplog.text
        assert weather_subroutine.manageable

        with pytest.raises(ValueError, match="'storm' has no linked actuator"):
            weather_subroutine.get_actuator_group_for_parameter("storm")

        ecosystem.config.delete_weather_parameter("storm")

    async def test_create_job(self, weather_subroutine: Weather):
        # Setup
        await weather_subroutine._activate_actuator_handler("rain")

        actuator_handler = weather_subroutine.get_actuator_handler("rainer")

        assert actuator_handler.mode is gv.ActuatorMode.automatic
        assert not actuator_handler.status

        # Test creating job
        job = weather_subroutine._create_job_func(
            job_name="test_job",
            actuator_handler=actuator_handler,
            duration=0.05,
            level=30.0
        )
        assert callable(job)

        await job()

        # The timer should be set
        timer = weather_subroutine._timers["test_job"]
        assert isinstance(timer, Timer)
        # The actuator handler should be manual and on
        assert actuator_handler.mode is gv.ActuatorMode.manual
        assert actuator_handler.status is True
        assert actuator_handler.level == 30.0

        await asyncio.sleep(0.10)

        # The timer should be removed
        assert "test_job" not in weather_subroutine._timers
        # The actuator handler should be back to its former state
        assert actuator_handler.mode is gv.ActuatorMode.automatic
        assert actuator_handler.status is False
        assert actuator_handler.level == 0.0

        await weather_subroutine._inactivate_actuator_handler("rain")

    async def test_add_job(self, weather_subroutine: Weather):
        # Setup
        parameter = "rain"
        await weather_subroutine._activate_actuator_handler(parameter)

        # Test adding job
        await weather_subroutine._add_job(parameter)

        identifier = f"{weather_subroutine.ecosystem.uid}-weather_routine-{parameter}"
        assert weather_subroutine.ecosystem.engine.scheduler.get_job(identifier) is not None
        assert weather_subroutine._jobs == {parameter}
        # The job hasn't started so the timer should be empty
        assert weather_subroutine._timers == {}

        # Should raise if the job already exists
        with pytest.raises(ValueError):
            await weather_subroutine._add_job(parameter)

        # Test removing job
        await weather_subroutine._remove_job("rain")

        # Should raise if the job does not exist
        with pytest.raises(ValueError):
            await weather_subroutine._remove_job("rain")

        await weather_subroutine._inactivate_actuator_handler("rain")
