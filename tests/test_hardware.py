import pytest

from gaia.config import EcosystemConfig
from gaia.exceptions import HardwareNotFound

from .data import hardware_address, sensor_info, sensor_uid


hardware_info = sensor_info
hardware_uid = sensor_uid


def test_hardware_creation_fail_address(ecosystem_config: EcosystemConfig):
    with pytest.raises(ValueError, match=r"Address .* already used"):
        ecosystem_config.create_new_hardware(**hardware_info)


def test_hardware_creation_fail_model(ecosystem_config: EcosystemConfig):
    invalid_hardware_info = {
        **hardware_info,
        "address": "GPIO_11",  # Use a free address
        "model": "Invalid"
    }
    with pytest.raises(ValueError, match="This hardware model is not supported"):
        ecosystem_config.create_new_hardware(**invalid_hardware_info)


def test_hardware_creation_fail_type(ecosystem_config: EcosystemConfig):
    invalid_hardware_info = {
        **hardware_info,
        "address": "GPIO_7",  # Use a free address
        "type": "Invalid",
    }
    error_msg = "VALUE ERROR at parameter 'type', input 'Invalid' is not valid"
    with pytest.raises(ValueError, match=error_msg):
        ecosystem_config.create_new_hardware(**invalid_hardware_info)


def test_hardware_creation_fail_level(ecosystem_config: EcosystemConfig):
    invalid_hardware_info = {
        **hardware_info,
        "address": "GPIO_7",  # Use a free address
        "level": "Invalid",
    }
    error_msg = "VALUE ERROR at parameter 'level', input 'Invalid' is not valid"
    with pytest.raises(ValueError, match=error_msg):
        ecosystem_config.create_new_hardware(**invalid_hardware_info)


def test_hardware_creation_success(ecosystem_config: EcosystemConfig):
    valid_hardware_info = {
        **hardware_info,
        "model": "gpioSwitch",
        "address": "GPIO_11",  # Use a free address
    }
    ecosystem_config.create_new_hardware(**valid_hardware_info)


def test_hardware_update_fail_not_found(ecosystem_config: EcosystemConfig):
    with pytest.raises(HardwareNotFound):
        ecosystem_config.update_hardware("invalid_uid", address="GPIO_7")


def test_hardware_update_fail_address(ecosystem_config: EcosystemConfig):
    with pytest.raises(ValueError, match=r"Address .* already used"):
        ecosystem_config.update_hardware(hardware_uid, address=hardware_address)


def test_hardware_update_fail_model(ecosystem_config: EcosystemConfig):
    with pytest.raises(ValueError, match="This hardware model is not supported"):
        ecosystem_config.update_hardware(hardware_uid, model="Invalid")


def test_hardware_update_fail_type(ecosystem_config: EcosystemConfig):
    error_msg = "VALUE ERROR at parameter 'type', input 'Invalid' is not valid"
    with pytest.raises(ValueError, match=error_msg):
        ecosystem_config.update_hardware(hardware_uid, type="Invalid")


def test_hardware_update_fail_level(ecosystem_config: EcosystemConfig):
    error_msg = "VALUE ERROR at parameter 'level', input 'Invalid' is not valid"
    with pytest.raises(ValueError, match=error_msg):
        ecosystem_config.update_hardware(hardware_uid, level="Invalid")


def test_hardware_update_success(ecosystem_config: EcosystemConfig):
    ecosystem_config.update_hardware(
        hardware_uid, model="gpioSwitch", address="GPIO_11")


def test_hardware_delete_fail_not_found(ecosystem_config: EcosystemConfig):
    with pytest.raises(HardwareNotFound):
        ecosystem_config.delete_hardware("invalid_uid")


def test_hardware_delete_success(ecosystem_config: EcosystemConfig):
    ecosystem_config.delete_hardware(hardware_uid)
