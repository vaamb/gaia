from __future__ import annotations

from asyncio import sleep
import math

import pytest

import gaia_validators as gv

from gaia.actuator_handler import ActuatorHandler, Timer
from gaia.events import Events
from gaia.hardware.abc import DimmableSwitchMixin

import tests.data as test_data
from tests.utils import yield_control


hardware_dict = {
    test_data.light_uid: test_data.light_info,
}


@pytest.mark.asyncio
class TestTimer:
    async def test_timer(self):
        x = False

        async def set_to_true():
            nonlocal x
            x = True

        countdown = 0.25
        timer = Timer(set_to_true, countdown)
        assert math.isclose(timer.time_left(), countdown, abs_tol=0.01)

        await sleep(countdown + 0.1)
        assert x is True


@pytest.mark.asyncio
@pytest.mark.parametrize("ecosystem", [{"hardware": hardware_dict}], indirect=True)
class TestActuatorHandler:
    async def test_linked_actuators(self, light_handler: ActuatorHandler):
        light: DimmableSwitchMixin = light_handler.ecosystem.hardware[test_data.light_uid]

        linked_actuators = light_handler.get_linked_actuators()

        assert len(linked_actuators) == 1
        assert linked_actuators[0] == light

    async def test_status(self, light_handler: ActuatorHandler):
        light: DimmableSwitchMixin = light_handler.ecosystem.hardware[test_data.light_uid]

        # Test default status
        assert not light_handler.status
        assert (await light.get_status()) is False

        # Test set status True
        async with light_handler.update_status_transaction():
            success = await light_handler.set_status(True)
        assert success
        assert light_handler.status
        assert (await light.get_status()) is True

        # Test set status False
        async with light_handler.update_status_transaction():
            success =await light_handler.set_status(False)
        assert success
        assert not light_handler.status
        assert (await light.get_status()) is False

    async def test_level(self, light_handler: ActuatorHandler):
        light: DimmableSwitchMixin = light_handler.ecosystem.hardware[test_data.light_uid]

        # Test default level
        assert light_handler.level == 0.0
        assert (await light.get_pwm_level()) == 0

        # Test level > 0
        async with light_handler.update_status_transaction():
            success = await light_handler.set_level(42)
        assert success
        assert light_handler.level == 42
        assert (await light.get_pwm_level()) == 42

        # Test level = 0
        async with light_handler.update_status_transaction():
            success = await light_handler.set_level(0)
        assert success
        assert light_handler.level == 0
        assert (await light.get_pwm_level()) == 0.0

    async def test_handler_timer_modification(self, light_handler: ActuatorHandler):
        # Test default countdown
        assert light_handler.countdown is None

        # Test setup countdown
        timer = 1.0
        async with light_handler.update_status_transaction():
            await light_handler.turn_to(gv.ActuatorModePayload.on, countdown=timer)
        assert math.isclose(light_handler.countdown, timer, abs_tol=0.015)

        # Test setup countdown
        increase = 0.50
        timer += increase  # remaining ~ 1.50 sec
        async with light_handler.update_status_transaction():
            light_handler.increase_countdown(increase)
        assert math.isclose(light_handler.countdown, timer, abs_tol=0.015)

        # Test decrease countdown
        decrease = 0.75
        timer -= decrease  # remaining ~ 0.75 sec
        async with light_handler.update_status_transaction():
            light_handler.decrease_countdown(decrease)
        assert math.isclose(light_handler.countdown, timer, abs_tol=0.015)

        # Test sleep, remaining above 0
        decrease = 0.15
        timer -= decrease  # remaining ~ 0.60 sec
        await sleep(decrease)
        assert math.isclose(light_handler.countdown, timer, abs_tol=0.015)

        # Test sleep, remaining under 0
        decrease = 0.65
        timer -= decrease  # remaining ~ -0.05 sec
        assert timer < 0
        await sleep(decrease)
        assert not light_handler.countdown  # Either None or 0.0

    async def test_handler_timer_reset(self, light_handler: ActuatorHandler):
        # Test reset timer
        async with light_handler.update_status_transaction():
            await light_handler.turn_to(gv.ActuatorModePayload.on, countdown=1.0)

        await yield_control()
        assert light_handler.countdown > 0.0

        async with light_handler.update_status_transaction():
            light_handler.reset_timer()
        assert light_handler.countdown is None

    async def test_turn_to(
            self,
            light_handler: ActuatorHandler,
            registered_events_handler: Events,
    ):
        # Test default state
        assert light_handler.status is False
        assert light_handler.mode is gv.ActuatorMode.automatic

        # Test turn on
        async with light_handler.update_status_transaction():
            await light_handler.turn_to(gv.ActuatorModePayload.on)
        assert light_handler.status is True
        assert light_handler.mode is gv.ActuatorMode.manual

        await yield_control()  # Allow the send data task to be processed
        event_payload = registered_events_handler.dispatcher.emit_store[0]
        assert event_payload["event"] == "actuators_data"
        ecosystem_payload = event_payload["data"][0]
        assert ecosystem_payload["uid"] == test_data.ecosystem_uid
        actuator_payload: gv.TurnActuatorPayload = ecosystem_payload["data"][0]
        assert actuator_payload[0] == light_handler.type      # Hardware type
        assert actuator_payload[1] == light_handler.group     # Actuator group
        assert actuator_payload[2] is True                    # Actuator active status
        assert actuator_payload[3] == gv.ActuatorMode.manual  # Actuator mode
        assert actuator_payload[4] is True                    # Actuator status

        # Test turn off
        async with light_handler.update_status_transaction():
            await light_handler.turn_to(gv.ActuatorModePayload.off)
        assert light_handler.status is False
        assert light_handler.mode is gv.ActuatorMode.manual

        await yield_control()  # Allow the send data task to be processed
        actuator_payload = registered_events_handler.dispatcher.emit_store[1]["data"][0]["data"][0]
        assert actuator_payload[0] == light_handler.type      # Hardware type
        assert actuator_payload[1] == light_handler.group     # Actuator group
        assert actuator_payload[2] is True                    # Actuator active status
        assert actuator_payload[3] == gv.ActuatorMode.manual  # Actuator mode
        assert actuator_payload[4] is False                   # Actuator status

        # Test turn automatic
        async with light_handler.update_status_transaction():
            await light_handler.turn_to(gv.ActuatorModePayload.automatic)
        # Light handler status changes throughout the day, cannot test it
        assert light_handler.mode is gv.ActuatorMode.automatic

        await yield_control()  # Allow the send data task to be processed
        actuator_payload = registered_events_handler.dispatcher.emit_store[2]["data"][0]["data"][0]
        assert actuator_payload[0] == light_handler.type         # Hardware type
        assert actuator_payload[1] == light_handler.group        # Actuator group
        assert actuator_payload[2] is True                       # Actuator active status
        assert actuator_payload[3] == gv.ActuatorMode.automatic  # Actuator mode
        assert actuator_payload[4] is False                      # Actuator status

        # Test countdown
        countdown = 0.02
        async with light_handler.update_status_transaction():
            await light_handler.turn_to(gv.ActuatorModePayload.on, countdown=countdown)
        assert light_handler.mode is gv.ActuatorMode.automatic  # Make sure it hasn't changed yet
        assert math.isclose(light_handler.countdown, countdown, abs_tol=0.001)
        assert len(registered_events_handler.dispatcher.emit_store) == 3

        await sleep(countdown + 0.01)  # Allow the countdown to finish
        assert light_handler.status is True
        assert light_handler.mode is gv.ActuatorMode.manual

        await yield_control()  # Allow the send data task to be processed
        actuator_payload = registered_events_handler.dispatcher.emit_store[3]["data"][0]["data"][0]
        assert actuator_payload[0] == light_handler.type      # Hardware type
        assert actuator_payload[1] == light_handler.group     # Actuator group
        assert actuator_payload[2] is True                    # Actuator active status
        assert actuator_payload[3] == gv.ActuatorMode.manual  # Actuator mode
        assert actuator_payload[4] is True                    # Actuator status
