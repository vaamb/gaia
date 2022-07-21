from datetime import time
import typing as t

import pytest

from src.config_parser import GeneralConfig, get_IDs
from src.exceptions import UndefinedParameter

from .utils import ECOSYSTEM_UID, TESTING_ECOSYSTEM_CFG


if t.TYPE_CHECKING:
    from src.config_parser import SpecificConfig


# ---------------------------------------------------------------------------
#   Test GeneralConfig
# ---------------------------------------------------------------------------
def test_general_config_singleton(temp_dir, general_config: GeneralConfig):
    assert general_config is GeneralConfig(temp_dir)


def test_config_files_created(general_config: GeneralConfig):
    for cfg in ("ecosystems", "private"):
        cfg_file = general_config._base_dir/f"{cfg}.cfg"
        assert cfg_file.is_file()


def test_config_files_watchdog(general_config: GeneralConfig):
    general_config.start_watchdog()
    for cfg in general_config._hash_dict:
        assert(isinstance(general_config._hash_dict[cfg], str))
    general_config.stop_watchdog()


def test_save_reload(general_config: GeneralConfig):
    ecosystems_cfg = general_config.ecosystems_config
    private_config = general_config.private_config
    general_config.save(("ecosystems", "private"))
    general_config.reload(("ecosystems", "private"))
    assert general_config.ecosystems_config == ecosystems_cfg
    assert general_config.private_config == private_config


def test_properties(temp_dir, general_config: GeneralConfig):
    assert str(general_config.base_dir) == str(temp_dir)
    assert general_config.ecosystems_uid == [ECOSYSTEM_UID]
    assert general_config.ecosystems_name == [
        TESTING_ECOSYSTEM_CFG[ECOSYSTEM_UID]["name"]
    ]
    status = general_config.ecosystems_config[ECOSYSTEM_UID]["status"]
    general_config.ecosystems_config[ECOSYSTEM_UID]["status"] = True
    assert general_config.get_ecosystems_expected_running() == \
           {ECOSYSTEM_UID}
    general_config.ecosystems_config[ECOSYSTEM_UID]["status"] = status


def test_get_IDs():
    assert get_IDs(ECOSYSTEM_UID).uid == ECOSYSTEM_UID
    assert get_IDs(ECOSYSTEM_UID) == get_IDs(
        TESTING_ECOSYSTEM_CFG[ECOSYSTEM_UID]["name"]
    )
    with pytest.raises(ValueError):
        get_IDs("not in config")


def test_home(general_config: GeneralConfig):
    with pytest.raises(UndefinedParameter):
        general_config.home_city
        general_config.home_coordinates
    general_config.home_city = "Bruxelles"
    # general_config.home_coordinates["latitude"] = 50.8465573


# ---------------------------------------------------------------------------
#   Test SpecificConfig
# ---------------------------------------------------------------------------
def test_specific_properties(specific_config: "SpecificConfig"):
    assert specific_config.ecosystem_config == TESTING_ECOSYSTEM_CFG[ECOSYSTEM_UID]
    assert specific_config.name == "test"
    specific_config.name = "name"
    assert specific_config.name == "name"
    assert specific_config.status is False
    specific_config.status = True
    assert specific_config.status is True
    assert not specific_config.get_managed_subroutines()
    for management in (
            "sensors", "light", "climate", "watering", "health", "alarms",
            "webcam"
    ):
        specific_config.set_management(management, True)
        assert specific_config.get_management(management)


def test_specific_light(specific_config: "SpecificConfig"):
    with pytest.raises(UndefinedParameter):
        specific_config.light_method
    specific_config.set_management("light", True)
    with pytest.raises(UndefinedParameter):
        specific_config.light_method
    specific_config.light_method = "fixed"


def test_specific_chaos(specific_config: "SpecificConfig"):
    with pytest.raises(UndefinedParameter):
        specific_config.chaos
    parameters = {"frequency": 10, "duration": 2, "intensity": 1.2}
    specific_config.chaos = parameters
    assert specific_config.chaos == parameters


def test_specific_climate(specific_config: "SpecificConfig"):
    with pytest.raises(UndefinedParameter):
        specific_config.get_climate_parameters("temperature")
    parameters = {"day": 25, "night": 20, "hysteresis": 1}
    specific_config.set_climate_parameters("temperature", parameters)
    assert specific_config.get_climate_parameters("temperature") == parameters


def test_specific_time_parameters(specific_config: "SpecificConfig"):
    with pytest.raises(UndefinedParameter):
        specific_config.time_parameters
    with pytest.raises(ValueError):
        specific_config.time_parameters = {"wrong": "dict"}
    specific_config.time_parameters = {"day": "6h00", "night": "22h00"}
    assert specific_config.time_parameters["day"] == time(6, 0)
    with pytest.raises(UndefinedParameter):
        specific_config.sun_times


def test_specific_hardware(specific_config: "SpecificConfig"):
    hardware_info = {
        "name": "test",
        "address": "GPIO_10",
        "model": "DHT22",
        "type": "sensor",
        "level": "environment",
        "measure": ["temperature"],
        "plant": "testPlant",
    }
    specific_config.create_new_hardware(**hardware_info)
    with pytest.raises(ValueError):
        specific_config.create_new_hardware(**hardware_info)
        hardware_info["model"] = "DoesNotExist"
        specific_config.create_new_hardware(**hardware_info)
    specific_config.ecosystem_config = TESTING_ECOSYSTEM_CFG[ECOSYSTEM_UID]
