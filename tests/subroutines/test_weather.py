import asyncio

import pytest

import gaia_validators as gv

from gaia import Ecosystem
from gaia.actuator_handler import Timer
from gaia.subroutines.weather import Weather

from ..data import humidifier_uid


@pytest.mark.asyncio
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
        ecosystem.config.delete_hardware(humidifier_uid)
        await ecosystem.refresh_hardware()
        assert not weather_subroutine.manageable

    def test_hardware_needed(self, weather_subroutine: Weather):
        uids = weather_subroutine.get_hardware_needed_uid()
        assert uids == {humidifier_uid}

    async def test_routine(self, weather_subroutine: Weather):
        # Enable the subroutine
        weather_subroutine.enable()

        assert not weather_subroutine._actuator_handlers
        assert not weather_subroutine._jobs

        # Test start, refresh and stop (weather has no routine)
        await weather_subroutine.start()

        assert weather_subroutine._actuator_handlers
        assert weather_subroutine._jobs

        with pytest.raises(ValueError):
            await weather_subroutine.routine()

        await weather_subroutine.refresh()

        await weather_subroutine.stop()

        # Disable the subroutine
        weather_subroutine.disable()

    async def test_mount_actuator_handler(self, weather_subroutine: Weather):
        # Setup
        weather_subroutine._actuator_handlers = {}

        # Test mounting actuator handler
        await weather_subroutine._mount_actuator_handler("rain")

        # Rainer is the actuator group for the rain parameter
        actuator_group = weather_subroutine.get_actuator_group_for_parameter("rain")
        assert weather_subroutine.actuator_handlers["rain"].group == actuator_group

        # Should raise if the actuator handler is already mounted
        with pytest.raises(ValueError):
            await weather_subroutine._mount_actuator_handler("rain")

        # Test unmounting actuator handler
        await weather_subroutine._unmount_actuator_handler("rain")

        assert "rain" not in weather_subroutine.actuator_handlers

        # Should raise if the actuator handler is not mounted
        with pytest.raises(ValueError):
            await weather_subroutine._unmount_actuator_handler("rain")

    async def test_create_job(self, weather_subroutine: Weather):
        # Setup
        weather_subroutine._actuator_handlers = {}
        await weather_subroutine._mount_actuator_handler("rain")

        actuator_handler = weather_subroutine.actuator_handlers["rain"]

        assert actuator_handler.mode is gv.ActuatorMode.automatic
        assert not actuator_handler.status

        # Test creating job
        job = weather_subroutine._create_job_func(
            job_name="test_job",
            actuator_handler=actuator_handler,
            duration=0.5,
            level=100.0
        )
        assert callable(job)

        await job()

        # The timer should be set
        timer = weather_subroutine._timers["test_job"]
        assert isinstance(timer, Timer)
        # The actuator handler should be manual and on
        assert actuator_handler.mode is gv.ActuatorMode.manual
        assert actuator_handler.status

        await asyncio.sleep(1.0)

        # The timer should be removed
        assert "test_job" not in weather_subroutine._timers
        # The actuator handler should be back to its former state
        assert actuator_handler.mode is gv.ActuatorMode.automatic
        assert not actuator_handler.status

    async def test_add_job(self, weather_subroutine: Weather):
        # Setup
        weather_subroutine._actuator_handlers = {}
        await weather_subroutine._mount_actuator_handler("rain")

        # Test adding job
        await weather_subroutine._add_job("rain")

        assert weather_subroutine.ecosystem.engine.scheduler.get_job("rain") is not None
        assert weather_subroutine._jobs == {"rain"}
        # The job hasn't started so the timer should be empty
        assert weather_subroutine._timers == {}

        # Should raise if the job already exists
        with pytest.raises(ValueError):
            await weather_subroutine._add_job("rain")

        # Test removing job
        await weather_subroutine._remove_job("rain")

        # Should raise if the job does not exist
        with pytest.raises(ValueError):
            await weather_subroutine._remove_job("rain")
