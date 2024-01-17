from __future__ import annotations

import math
from time import sleep

import pytest

import gaia_validators as gv

from gaia.actuator_handler import ActuatorHandler
from gaia.hardware import gpioDimmable, gpioSwitch, Hardware

from .data import light_uid


def get_lights() -> list[gpioDimmable | gpioSwitch]:
    return [  # type: ignore
        hardware for hardware in Hardware.get_mounted().values()
        if hardware.uid == light_uid
    ]


def test_status(light_handler: ActuatorHandler):
    # Test default status
    assert not light_handler.status
    for light in get_lights():
        light: gpioSwitch
        assert light.pin.value() == 0

    # Test set status True
    light_handler.set_status(True)
    assert light_handler.status
    for light in get_lights():
        light: gpioSwitch
        assert light.pin.value() == 1

    # Test set status False
    light_handler.set_status(False)
    assert not light_handler.status
    for light in get_lights():
        light: gpioSwitch
        assert light.pin.value() == 0


def test_level(light_handler: ActuatorHandler):
    # Test default level
    assert light_handler.level is None
    for light in get_lights():
        light: gpioDimmable
        assert light.dimmer.duty_cycle == 0

    # Test level > 0
    light_handler.set_level(42)
    assert light_handler.level == 42
    for light in get_lights():
        light: gpioDimmable
        assert light.dimmer.duty_cycle > 0.0

    # Test level = 0
    light_handler.set_level(0)
    assert light_handler.level == 0
    for light in get_lights():
        light: gpioDimmable
        assert light.dimmer.duty_cycle == 0.0


def test_timer(light_handler: ActuatorHandler):
    # Test default countdown
    assert light_handler.countdown is None

    # Test increase countdown
    timer = 1.0
    light_handler.increase_countdown(timer)
    assert math.isclose(light_handler.countdown, timer, abs_tol=0.01)

    # Test decrease countdown
    decrease = 0.75
    timer -= decrease
    light_handler.decrease_countdown(decrease)
    assert math.isclose(light_handler.countdown, timer, abs_tol=0.01)

    # Test sleep, remaining above 0
    decrease = 0.15
    timer -= decrease
    sleep(decrease)
    assert math.isclose(light_handler.countdown, timer, abs_tol=0.01)

    # Test sleep, remaining under 0
    decrease = 0.15
    timer -= decrease
    assert timer < 0
    sleep(decrease)
    assert light_handler.countdown == 0.0

    # Test reset countdown
    light_handler.reset_countdown()
    assert light_handler.countdown is None


@pytest.mark.skip
def test_turn_to(light_handler: ActuatorHandler):
    ecosystem = light_handler.ecosystem
    hardware = ecosystem.subroutines["light"].hardware

    # Test default state
    assert light_handler.status is False
    assert light_handler.mode is gv.ActuatorMode.automatic

    # Test turn on
    light_handler.turn_to(gv.ActuatorModePayload.on)
    assert light_handler.status is True
    assert light_handler.mode is gv.ActuatorMode.manual

    # Test turn automatic
    light_handler.turn_to(gv.ActuatorModePayload.automatic)
    assert light_handler.mode is gv.ActuatorMode.automatic

    # Test turn off
    light_handler.turn_to(gv.ActuatorModePayload.off)
    assert light_handler.status is False
    assert light_handler.mode is gv.ActuatorMode.manual

    # Test turn with str
    light_handler.turn_to("automatic")

    # Test countdown
    light_handler.turn_to(gv.ActuatorModePayload.on, countdown=0.25)
    assert light_handler.status is True
    assert light_handler.mode is gv.ActuatorMode.manual
    assert math.isclose(light_handler.countdown, 0.25, abs_tol=0.001)

    sleep(0.5)
    # Process all the countdown associated timing info
    light_handler.check_countdown()
    assert light_handler.mode is gv.ActuatorMode.automatic
