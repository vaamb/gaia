from __future__ import annotations

from asyncio import sleep
import math

import pytest

import gaia_validators as gv

from gaia.actuator_handler import ActuatorHandler, Timer
from gaia.hardware import gpioDimmable, gpioSwitch, Hardware

from .data import light_uid


def get_lights() -> list[gpioDimmable | gpioSwitch]:
    return [  # type: ignore
        hardware
        for hardware in Hardware.get_mounted().values()
        if hardware.uid == light_uid
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
    for light in get_lights():
        light: gpioSwitch
        assert light.pin.value() == 0

    # Test set status True
    async with light_handler.update_status_transaction():
        await light_handler.set_status(True)
    assert light_handler.status
    for light in get_lights():
        light: gpioSwitch
        assert light.pin.value() == 1

    # Test set status False
    async with light_handler.update_status_transaction():
        await light_handler.set_status(False)
    assert not light_handler.status
    for light in get_lights():
        light: gpioSwitch
        assert light.pin.value() == 0


@pytest.mark.asyncio
async def test_level(light_handler: ActuatorHandler):
    # Test default level
    assert light_handler.level is None
    for light in get_lights():
        light: gpioDimmable
        assert light.dimmer.duty_cycle == 0

    # Test level > 0
    async with light_handler.update_status_transaction():
        await light_handler.set_level(42)
    assert light_handler.level == 42
    for light in get_lights():
        light: gpioDimmable
        assert light.dimmer.duty_cycle > 0.0

    # Test level = 0
    async with light_handler.update_status_transaction():
        await light_handler.set_level(0)
    assert light_handler.level == 0
    for light in get_lights():
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
async def test_turn_to(light_handler: ActuatorHandler):
    # Test default state
    assert light_handler.status is False
    assert light_handler.mode is gv.ActuatorMode.automatic

    # Test turn on
    async with light_handler.update_status_transaction():
        await light_handler.turn_to(gv.ActuatorModePayload.on)
    assert light_handler.status is True
    assert light_handler.mode is gv.ActuatorMode.manual

    # Test turn off
    async with light_handler.update_status_transaction():
        await light_handler.turn_to(gv.ActuatorModePayload.off)
    assert light_handler.status is False
    assert light_handler.mode is gv.ActuatorMode.manual

    # Test turn automatic
    async with light_handler.update_status_transaction():
        await light_handler.turn_to(gv.ActuatorModePayload.automatic)
    # Light handler status changes throughout the time when tested
    assert light_handler.mode is gv.ActuatorMode.automatic

    # Test countdown
    countdown = 0.10
    async with light_handler.update_status_transaction():
        await light_handler.turn_to(gv.ActuatorModePayload.on, countdown=countdown)
    assert light_handler.mode is gv.ActuatorMode.automatic
    assert math.isclose(light_handler.countdown, countdown, abs_tol=0.001)

    await sleep(0.15)
    # Process all the countdown associated timing info
    assert light_handler.status is True
    assert light_handler.mode is gv.ActuatorMode.manual
