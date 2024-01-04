from datetime import time

import pytest

import gaia_validators as gv

from gaia import EngineConfig
from gaia.subroutines import Light

from ..data import (
    light_info, light_uid, lighting_start, lighting_stop, sensor_info, sensor_uid)
from ..utils import get_logs_content


def test_manageable(light_subroutine: Light):
    assert light_subroutine.manageable

    light_subroutine.ecosystem.config.delete_hardware(light_uid)

    assert not light_subroutine.manageable


def test_expected_status(light_subroutine: Light):
    expected_status = light_subroutine.expected_status

    lighting_hours = gv.LightingHours(
        morning_start=time(8),
        morning_end=time(10),
        evening_start=time(18),
        evening_end=time(20)
    )

    now = time(6)
    assert not expected_status(
        method=gv.LightMethod.elongate, lighting_hours=lighting_hours, _now=now)
    assert not expected_status(
        method=gv.LightMethod.fixed, lighting_hours=lighting_hours, _now=now)
    assert not expected_status(
        method=gv.LightMethod.mimic, lighting_hours=lighting_hours, _now=now)

    now = time(9)
    assert expected_status(
        method=gv.LightMethod.elongate, lighting_hours=lighting_hours, _now=now)
    assert expected_status(
        method=gv.LightMethod.fixed, lighting_hours=lighting_hours, _now=now)
    assert expected_status(
        method=gv.LightMethod.mimic, lighting_hours=lighting_hours, _now=now)

    now = time(11)
    assert not expected_status(
        method=gv.LightMethod.elongate, lighting_hours=lighting_hours, _now=now)
    assert expected_status(
        method=gv.LightMethod.fixed, lighting_hours=lighting_hours, _now=now)
    assert expected_status(
        method=gv.LightMethod.mimic, lighting_hours=lighting_hours, _now=now)

    now = time(21)
    assert not expected_status(
        method=gv.LightMethod.elongate, lighting_hours=lighting_hours, _now=now)
    assert not expected_status(
        method=gv.LightMethod.fixed, lighting_hours=lighting_hours, _now=now)
    assert not expected_status(
        method=gv.LightMethod.mimic, lighting_hours=lighting_hours, _now=now)


def test_hardware_needed(light_subroutine: Light):
    uids = light_subroutine.get_hardware_needed_uid()
    assert uids == {light_uid}


def test_add_hardware(light_subroutine: Light, engine_config: EngineConfig):
    light_subroutine.add_hardware(gv.HardwareConfig(uid=light_uid, **light_info))

    light_subroutine.add_hardware(gv.HardwareConfig(uid=sensor_uid, **sensor_info))
    with get_logs_content(engine_config.logs_dir / "base.log") as logs:
        assert "not in the list of the hardware available." in logs


def test_lighting_hours(light_subroutine: Light):
    assert light_subroutine.lighting_hours == gv.LightingHours(
        morning_start=lighting_start, evening_end=lighting_stop)


def test_turn_light(light_subroutine: Light):
    with pytest.raises(RuntimeError, match=r"Light subroutine is not started"):
        light_subroutine.turn_light(gv.ActuatorModePayload.on)

    light_subroutine.enable()
    light_subroutine.start()
    light_subroutine.turn_light(gv.ActuatorModePayload.on)
    light_subroutine.turn_light(gv.ActuatorModePayload.off)
    light_subroutine.turn_light(gv.ActuatorModePayload.automatic)

    with pytest.raises(ValueError):
        light_subroutine.turn_light("WrongMode")