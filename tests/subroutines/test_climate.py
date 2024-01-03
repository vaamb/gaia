import pytest

import gaia_validators as gv

from gaia import EngineConfig
from gaia.subroutines.sensors import Sensors
from gaia.subroutines.climate import Climate, CoupleDirection

from ..data import heater_info, heater_uid, sensor_info, sensor_uid
from ..utils import get_logs_content


def test_manageable(climate_subroutine: Climate):
    assert climate_subroutine.manageable

    # Make sure sensors subroutine is required
    climate_subroutine.ecosystem.stop_subroutine("sensors")
    assert not climate_subroutine.manageable

    climate_subroutine.ecosystem.start_subroutine("sensors")
    assert climate_subroutine.manageable

    # Make sure a climate parameter is needed
    climate_subroutine.ecosystem.config.delete_climate_parameter("temperature")
    assert not climate_subroutine.manageable

    climate_subroutine.ecosystem.config.set_climate_parameter(
        "temperature",
        {"day": 25, "night": 20, "hysteresis": 2}
    )
    assert climate_subroutine.manageable

    # Make sure a regulator is needed
    climate_subroutine.ecosystem.config.delete_hardware(heater_uid)
    assert not climate_subroutine.manageable


def test_expected_status(climate_subroutine: Climate):
    expected_status = climate_subroutine.expected_status
    target_value = 42.0
    hysteresis = 2.5

    current_value = target_value - 2 * hysteresis  # Under target
    couple_direction = CoupleDirection.increase

    # Test below target, actuator is increasing
    assert expected_status(
        current_value=current_value, target_value=target_value,
        hysteresis=hysteresis, couple_direction=couple_direction
    )

    # Test below target, actuator is decreasing
    assert not expected_status(
        current_value=current_value, target_value=target_value,
        hysteresis=hysteresis, couple_direction=CoupleDirection.decrease
    )

    # Test below target, in hysteresis range
    assert not expected_status(
        current_value=target_value-2.0, target_value=target_value,
        hysteresis=hysteresis, couple_direction=couple_direction
    )

    current_value = target_value + 2 * hysteresis  # Above target
    couple_direction = CoupleDirection.decrease

    # Test above target, actuator is decreasing
    assert expected_status(
        current_value=current_value, target_value=target_value,
        hysteresis=hysteresis, couple_direction=couple_direction
    )

    # Test above target, actuator is increasing
    assert not expected_status(
        current_value=current_value, target_value=target_value,
        hysteresis=hysteresis, couple_direction=CoupleDirection.increase
    )

    # Test above target, in hysteresis range
    assert not expected_status(
        current_value=target_value+2.0, target_value=target_value,
        hysteresis=hysteresis, couple_direction=couple_direction
    )

    # Test current_value == target_value
    assert not expected_status(
        current_value=target_value, target_value=target_value,
        hysteresis=hysteresis, couple_direction=couple_direction
    )

    # Test current_value is None
    assert not expected_status(
        current_value=None, target_value=target_value,
        hysteresis=hysteresis, couple_direction=couple_direction
    )

    # Test target_value is None
    assert not expected_status(
        current_value=current_value, target_value=None,
        hysteresis=hysteresis, couple_direction=couple_direction
    )

    # Test hysteresis is None
    assert expected_status(
        current_value=current_value, target_value=target_value,
        hysteresis=None, couple_direction=couple_direction
    )

    assert not expected_status(
        current_value=target_value, target_value=target_value,
        hysteresis=None, couple_direction=couple_direction
    )


def test_hardware_needed(climate_subroutine: Climate):
    uids = climate_subroutine.get_hardware_needed_uid()
    assert uids == {heater_uid}


def test_add_hardware(climate_subroutine: Climate, engine_config: EngineConfig):
    climate_subroutine.add_hardware(gv.HardwareConfig(uid=heater_uid, **heater_info))

    climate_subroutine.add_hardware(gv.HardwareConfig(uid=sensor_uid, **sensor_info))
    with get_logs_content(engine_config.logs_dir / "base.log") as logs:
        assert "not in the list of the hardware available." in logs


def test_turn_actuator(climate_subroutine: Climate):
    with pytest.raises(RuntimeError, match=r"Climate subroutine is not started"):
        climate_subroutine.turn_climate_actuator(
            gv.HardwareType.heater, gv.ActuatorModePayload.on)

    climate_subroutine.enable()
    climate_subroutine.start()
    climate_subroutine.turn_climate_actuator(
        gv.HardwareType.heater, gv.ActuatorModePayload.on)
    climate_subroutine.turn_climate_actuator(
        gv.HardwareType.heater, gv.ActuatorModePayload.off)
    climate_subroutine.turn_climate_actuator(
        gv.HardwareType.heater, gv.ActuatorModePayload.automatic)

    climate_subroutine.turn_climate_actuator(
        gv.HardwareType.cooler, gv.ActuatorModePayload.on)

    climate_subroutine.turn_climate_actuator(
        gv.HardwareType.humidifier, gv.ActuatorModePayload.on)

    climate_subroutine.turn_climate_actuator(
        gv.HardwareType.dehumidifier, gv.ActuatorModePayload.on)

    with pytest.raises(ValueError):
        climate_subroutine.turn_climate_actuator(
            "WrongHardwareType", gv.ActuatorModePayload.automatic)

    with pytest.raises(ValueError):
        climate_subroutine.turn_climate_actuator(
            gv.HardwareType.heater, "WrongMode")


def test_regulated_parameters(climate_subroutine: Climate):
    parameters = climate_subroutine.regulated_parameters
    assert parameters == []

    # Computing manageable updates the regulated parameters but when not started
    #  it should still return an empty list
    climate_subroutine.manageable
    parameters = climate_subroutine.regulated_parameters
    assert parameters == []

    climate_subroutine.enable()
    climate_subroutine.start()
    parameters = climate_subroutine.regulated_parameters
    assert parameters == ["temperature"]  # depends on hardware available


def test_safe_stop_from_sensors(
        climate_subroutine: Climate,
        sensors_subroutine: Sensors
):
    assert sensors_subroutine.enabled
    assert sensors_subroutine.started

    climate_subroutine.enable()
    climate_subroutine.start()

    assert climate_subroutine.enabled
    assert climate_subroutine.started

    sensors_subroutine.stop()

    assert climate_subroutine.enabled
    assert not climate_subroutine.started
    assert not climate_subroutine.manageable
