from __future__ import annotations

from asyncio import sleep
import math

import pytest

import gaia_validators as gv

from gaia import Ecosystem
from gaia.actuator_handler import ActuatorHandler, Timer
from gaia.events import Events
from gaia.hardware import gpioDimmable, gpioSwitch

from .data import ecosystem_uid


def get_lights(ecosystem: Ecosystem) -> list[gpioDimmable | gpioSwitch]:
    return [  # type: ignore
        hardware
        for hardware in ecosystem.hardware.values()
        if hardware.type == gv.HardwareType.light
    ]


@pytest.mark.asyncio
async def test_timer():
    x = False

    async def set_to_true():
        nonlocal x
        x = True

    countdown = 0.5
    timer = Timer(set_to_true, countdown)
    assert math.isclose(timer.time_left(), countdown, abs_tol=0.01)

    await sleep(countdown + 0.5)
    assert x is True


@pytest.mark.asyncio
async def test_status(light_handler: ActuatorHandler):
    # Test default status
    assert not light_handler.status
    for light in get_lights(light_handler.ecosystem):
        light: gpioSwitch
        assert light.pin.value() == 0

    # Test set status True
    async with light_handler.update_status_transaction():
        await light_handler.set_status(True)
    assert light_handler.status
    for light in get_lights(light_handler.ecosystem):
        light: gpioSwitch
        assert light.pin.value() == 1

    # Test set status False
    async with light_handler.update_status_transaction():
        await light_handler.set_status(False)
    assert not light_handler.status
    for light in get_lights(light_handler.ecosystem):
        light: gpioSwitch
        assert light.pin.value() == 0


@pytest.mark.asyncio
async def test_level(light_handler: ActuatorHandler):
    # Test default level
    assert light_handler.level is None
    for light in get_lights(light_handler.ecosystem):
        light: gpioDimmable
        assert light.dimmer.duty_cycle == 0

    # Test level > 0
    async with light_handler.update_status_transaction():
        await light_handler.set_level(42)
    assert light_handler.level == 42
    for light in get_lights(light_handler.ecosystem):
        light: gpioDimmable
        assert light.dimmer.duty_cycle > 0.0

    # Test level = 0
    async with light_handler.update_status_transaction():
        await light_handler.set_level(0)
    assert light_handler.level == 0
    for light in get_lights(light_handler.ecosystem):
        light: gpioDimmable
        assert light.dimmer.duty_cycle == 0.0


@pytest.mark.asyncio
async def test_handler_timer_modification(light_handler: ActuatorHandler):
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
    assert light_handler.countdown is None

@pytest.mark.asyncio
async def test_handler_timer_reset(light_handler: ActuatorHandler):
    # Test reset timer
    async with light_handler.update_status_transaction():
        await light_handler.turn_to(gv.ActuatorModePayload.on, countdown=1.0)

    await sleep(0.01)
    assert light_handler.countdown > 0.0

    async with light_handler.update_status_transaction():
        light_handler.reset_timer()
    assert light_handler.countdown is None


@pytest.mark.asyncio
async def test_turn_to(
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

    await sleep(0.01)  # Allow the send data task to be processed
    event_payload = registered_events_handler.dispatcher.emit_store[0]
    assert event_payload["event"] == "actuators_data"
    ecosystem_payload = event_payload["data"][0]
    assert ecosystem_payload["uid"] == ecosystem_uid
    actuator_payload = ecosystem_payload["data"][0]
    assert actuator_payload[0] == light_handler.type      # Hardware type
    assert actuator_payload[2] == gv.ActuatorMode.manual  # Actuator mode
    assert actuator_payload[3] is True                    # Actuator status

    # Test turn off
    async with light_handler.update_status_transaction():
        await light_handler.turn_to(gv.ActuatorModePayload.off)
    assert light_handler.status is False
    assert light_handler.mode is gv.ActuatorMode.manual

    await sleep(0.01)  # Allow the send data task to be processed
    actuator_payload = registered_events_handler.dispatcher.emit_store[1]["data"][0]["data"][0]
    assert actuator_payload[0] == light_handler.type      # Hardware type
    assert actuator_payload[2] == gv.ActuatorMode.manual  # Actuator mode
    assert actuator_payload[3] is False                   # Actuator status

    # Test turn automatic
    async with light_handler.update_status_transaction():
        await light_handler.turn_to(gv.ActuatorModePayload.automatic)
    # Light handler status changes throughout the day, cannot test it
    assert light_handler.mode is gv.ActuatorMode.automatic

    await sleep(0.01)  # Allow the send data task to be processed
    actuator_payload = registered_events_handler.dispatcher.emit_store[2]["data"][0]["data"][0]
    assert actuator_payload[0] == light_handler.type         # Hardware type
    assert actuator_payload[2] == gv.ActuatorMode.automatic  # Actuator mode
    assert actuator_payload[3] is False                      # Actuator status

    # Test countdown
    countdown = 0.05
    async with light_handler.update_status_transaction():
        await light_handler.turn_to(gv.ActuatorModePayload.on, countdown=countdown)
    assert light_handler.mode is gv.ActuatorMode.automatic  # Make sure it hasn't changed yet
    assert math.isclose(light_handler.countdown, countdown, abs_tol=0.001)
    assert len(registered_events_handler.dispatcher.emit_store) == 3

    await sleep(0.06)  # Allow the countdown to finish
    assert light_handler.status is True
    assert light_handler.mode is gv.ActuatorMode.manual

    await sleep(0.01)  # Allow the send data task to be processed
    actuator_payload = registered_events_handler.dispatcher.emit_store[3]["data"][0]["data"][0]
    assert actuator_payload[0] == light_handler.type      # Hardware type
    assert actuator_payload[2] == gv.ActuatorMode.manual  # Actuator mode
    assert actuator_payload[3] is True                    # Actuator status
