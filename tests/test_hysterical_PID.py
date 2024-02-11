import gaia_validators as gv

from gaia import Ecosystem
from gaia.actuator_handler import HystericalPID


def test_hysterical_PID(ecosystem: Ecosystem):
    pid = HystericalPID(ecosystem.actuator_hub, gv.ClimateParameter.temperature)

    target_value = 42.0
    hysteresis = 2.5

    pid.target = target_value
    pid.hysteresis = hysteresis

    # Below target, out of hysteresis range
    current_value = target_value - 2 * hysteresis

    pid._last_output = -1.0
    assert pid.update_pid(current_value) > 0.0
    pid.reset()

    pid._last_output = 0.0
    assert pid.update_pid(current_value) > 0.0
    pid.reset()

    pid._last_output = 1.0
    assert pid.update_pid(current_value) > 0.0
    pid.reset()

    # Below target, in hysteresis range
    current_value = target_value - 0.5 * hysteresis

    pid._last_output = -1.0
    assert pid.update_pid(current_value) == 0.0
    pid.reset()

    pid._last_output = 0.0
    assert pid.update_pid(current_value) == 0.0
    pid.reset()

    pid._last_output = 1.0
    assert pid.update_pid(current_value) > 0.0
    pid.reset()

    # Above target, in hysteresis range
    current_value = target_value + 0.5 * hysteresis

    pid._last_output = -1.0
    assert pid.update_pid(current_value) < 0.0
    pid.reset()

    pid._last_output = 0.0
    assert pid.update_pid(current_value) == 0.0
    pid.reset()

    pid._last_output = 1.0
    assert pid.update_pid(current_value) == 0.0
    pid.reset()

    # Above target, out of hysteresis range
    current_value = target_value + 2 * hysteresis

    pid._last_output = -1.0
    assert pid.update_pid(current_value) < 0.0
    pid.reset()

    pid._last_output = 0.0
    assert pid.update_pid(current_value) < 0.0
    pid.reset()

    pid._last_output = 1.0
    assert pid.update_pid(current_value) < 0.0
    pid.reset()
