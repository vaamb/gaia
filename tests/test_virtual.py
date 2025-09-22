from math import isclose
from typing import cast

import pytest

import gaia_validators as gv

from gaia import Ecosystem
from gaia.hardware.abc import BaseSensor
from gaia.virtual import VirtualWorld, VirtualEcosystem

from tests.data import sensor_uid


@pytest.mark.asyncio
class TestVirtualEcosystem:
    async def test_initialization(
            self,
            virtual_world: VirtualWorld,
            virtual_ecosystem: VirtualEcosystem,
            ecosystem: Ecosystem,
    ):
        # Check the relationships
        assert virtual_ecosystem.virtual_world is virtual_world
        assert virtual_ecosystem is ecosystem.virtual_self

        # Check that virtual_ecosystem was started by the ecosystem
        assert virtual_ecosystem._start_time is not None
        assert virtual_ecosystem._hybrid_capacity is not None
        assert virtual_ecosystem._heat_quantity is not None
        assert virtual_ecosystem._humidity_quantity is not None

        # Make sure initial measures come from the virtual world
        assert isclose(virtual_ecosystem.temperature, virtual_world.temperature, rel_tol=0.01)
        assert isclose(virtual_ecosystem.humidity, virtual_world.humidity, rel_tol=0.01)
        assert isclose(virtual_ecosystem.light, virtual_world.light, rel_tol=0.01)

    async def test_actuators_virtualization(
            self,
            ecosystem: Ecosystem,
            virtual_ecosystem: VirtualEcosystem,
    ):
        # Setup humidity actuator
        actuator_couples = ecosystem.config.get_actuator_couples()
        group = actuator_couples[gv.ClimateParameter.humidity].increase
        actuator = ecosystem.actuator_hub.get_handler(group)
        async with actuator.update_status_transaction(activation=True):
            actuator.activate()

        # Test that turning on the actuator increases the humidity level
        humidity_start = virtual_ecosystem._humidity_quantity

        async with actuator.update_status_transaction():
            await actuator.turn_on()

        virtual_ecosystem.measure(3000.0)

        # The humidity should have more than doubled
        assert virtual_ecosystem.humidity > humidity_start * 2

        async with actuator.update_status_transaction():
            actuator.deactivate()

    async def test_sensors_virtualization(
            self,
            virtual_ecosystem: VirtualEcosystem,
            ecosystem: Ecosystem,
    ):
        virtual_DHT22: BaseSensor = cast(BaseSensor, ecosystem.hardware[sensor_uid])

        # Get sensor data for the humidity
        sensor_data: list[gv.SensorRecord] = await virtual_DHT22.get_data()
        humidity_record = [
            record
            for record in sensor_data
            if record.measure == "humidity"
        ][0]
        sensor_humidity: float =  humidity_record.value

        # Some noise is added to the true value using a sigma of 0.01
        assert isclose(sensor_humidity, virtual_ecosystem.humidity, rel_tol=0.05)
