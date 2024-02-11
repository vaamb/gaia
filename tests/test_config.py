from datetime import date, datetime, time, timedelta, timezone
from time import sleep

import pytest

import gaia_validators as gv

from gaia.config import CacheType, ConfigType, EcosystemConfig, EngineConfig
from gaia.exceptions import UndefinedParameter
from gaia.subroutines import subroutine_names
from gaia.utils import is_connected, yaml

from .data import ecosystem_info, ecosystem_name, sun_times
from .utils import get_logs_content


def is_not_connected(*args):
    return not is_connected()


# ---------------------------------------------------------------------------
#   Test EngineConfig
# ---------------------------------------------------------------------------
def test_engine_config_singleton(engine_config: EngineConfig):
    assert engine_config is EngineConfig()


def test_config_initialization(engine_config: EngineConfig):
    for cfg_type in ConfigType:
        cfg_path = engine_config.get_file_path(cfg_type)
        assert cfg_path.exists()
        assert cfg_path.is_file()


def test_ecosystem_config_creation_deletion(engine_config: EngineConfig):
    engine_config.create_ecosystem("Already fading away")
    engine_config.delete_ecosystem("Already fading away")
    with pytest.raises(ValueError):
        engine_config.delete_ecosystem("Already fading away")


def test_config_files_watchdog(engine_config: EngineConfig):
    # Start watchdog and make sure it can only be started once
    engine_config.start_watchdog()
    with get_logs_content(engine_config.logs_dir / "gaia.log") as logs:
        assert "Starting the configuration files watchdog" in logs
    with pytest.raises(RuntimeError):
        engine_config.start_watchdog()

    # Test watchdog for ecosystems cfg
    engine_config.create_ecosystem("Already fading away")
    with open(engine_config.config_dir / ConfigType.ecosystems.value, "w") as cfg:
        yaml.dump(engine_config.ecosystems_config_dict, cfg)
    sleep(0.15)  # Allow to check at least once if files changed. Rem: watcher period set to 0.10 sec
    with get_logs_content(engine_config.logs_dir / "gaia.log") as logs:
        assert "Updating ecosystems configuration" in logs

    # Test watchdog for private cfg
    engine_config.set_place(place="Nowhere", coordinates=(0.0, 0.0))
    with open(engine_config.config_dir / ConfigType.private.value, "w") as cfg:
        yaml.dump(engine_config.private_config, cfg)
    sleep(0.15)  # Allow to check at least once if files changed. Rem: watcher period set to 0.10 sec
    with get_logs_content(engine_config.logs_dir / "gaia.log") as logs:
        assert "Updating private configuration" in logs

    # Stop watchdog and make sure it can only be stopped once
    engine_config.stop_watchdog()
    with get_logs_content(engine_config.logs_dir / "gaia.log") as logs:
        assert "Stopping the configuration files watchdog" in logs
    with pytest.raises(RuntimeError):
        engine_config.stop_watchdog()

    # Restore config files
    with open(engine_config.config_dir / ConfigType.ecosystems.value, "w") as cfg:
        yaml.dump(ecosystem_info, cfg)
    with open(engine_config.config_dir / ConfigType.private.value, "w") as cfg:
        yaml.dump({}, cfg)


def test_save_load(engine_config: EngineConfig):
    ecosystems_cfg = engine_config.ecosystems_config
    private_config = engine_config.private_config
    for cfg_type in ConfigType:
        engine_config.save(cfg_type)
        engine_config.load(cfg_type)
    assert engine_config.ecosystems_config == ecosystems_cfg
    assert engine_config.private_config == private_config


def test_download_sun_times_no_coordinates(engine_config: EngineConfig):
    sun_times = engine_config.download_sun_times()
    assert sun_times is None
    with get_logs_content(engine_config.logs_dir / "gaia.log") as logs:
        assert "You need to define 'home' coordinates" in logs


@pytest.mark.skipif(is_not_connected)
@pytest.mark.timeout(5)
def test_download_sun_times_success(engine_config: EngineConfig):
    engine_config.home_coordinates = (0, 0)
    engine_config.download_sun_times()
    cached_result = engine_config.get_file_path(CacheType.sun_times)
    assert cached_result.exists()


def test_refresh_suntimes_not_needed(engine_config: EngineConfig):
    engine_config.home_coordinates = (0, 0)
    assert engine_config.home_sun_times is None
    engine_config.refresh_sun_times()
    with get_logs_content(engine_config.logs_dir / "gaia.log") as logs:
        assert "No need to refresh sun times" in logs
    assert engine_config.home_sun_times is None


@pytest.mark.skipif(is_not_connected)
def test_refresh_suntimes_success(
        engine_config: EngineConfig,
        ecosystem_config: EcosystemConfig,
):
    engine_config.home_coordinates = (0, 0)
    ecosystem_config.sky["lighting"] = gv.LightMethod.elongate
    assert engine_config.home_sun_times is None
    engine_config.refresh_sun_times()
    with get_logs_content(engine_config.logs_dir / "gaia.log") as logs:
        assert "successfully updated" in logs
    assert engine_config.home_sun_times is not None
    engine_config.refresh_sun_times()
    with get_logs_content(engine_config.logs_dir / "gaia.log") as logs:
        assert "Sun times already up to date" in logs
    assert engine_config.home_sun_times is not None


def test_status(engine_config: EngineConfig, ecosystem_config: EcosystemConfig):
    assert engine_config.ecosystems_name == [ecosystem_name]
    assert engine_config.get_ecosystems_expected_to_run() == set()
    ecosystem_config.status = True
    assert engine_config.get_ecosystems_expected_to_run() == {ecosystem_config.uid}


def test_get_IDs(engine_config: EngineConfig, ecosystem_config: EcosystemConfig):
    assert engine_config.get_IDs(ecosystem_name).uid == ecosystem_config.uid
    assert engine_config.get_IDs(ecosystem_name).name == ecosystem_config.name
    with pytest.raises(ValueError):
        engine_config.get_IDs("not in config")


def test_home(engine_config: EngineConfig):
    with pytest.raises(UndefinedParameter):
        engine_config.home_coordinates
    engine_config.home_coordinates = (4, 2)
    assert engine_config.home_coordinates.latitude == 4.0
    assert engine_config.home_coordinates.longitude == 2.0


# ---------------------------------------------------------------------------
#   Test EcosystemConfig
# ---------------------------------------------------------------------------
def test_ecosystem_config_singleton(ecosystem_config: EcosystemConfig):
    assert ecosystem_config is EcosystemConfig(ecosystem_name)


def test_ecosystem_config_dict(
        engine_config: EngineConfig,
        ecosystem_config: EcosystemConfig,
):
    assert ecosystem_config.general.__dict__ is engine_config.__dict__
    assert ecosystem_config._EcosystemConfig__dict is \
           engine_config.ecosystems_config_dict[ecosystem_config.uid]


def test_ecosystem_config_name(ecosystem_config: EcosystemConfig):
    assert ecosystem_config.name == ecosystem_name
    ecosystem_config.name = "name"
    assert ecosystem_config.name == "name"


def test_ecosystem_config_status(ecosystem_config: EcosystemConfig):
    assert ecosystem_config.status is False
    ecosystem_config.status = True
    assert ecosystem_config.status is True


def test_ecosystem_config_managed_subroutines(ecosystem_config: EcosystemConfig):
    assert not ecosystem_config.get_subroutines_enabled()

    for management in gv.ManagementFlags:
        ecosystem_config.set_management(management, True)
        assert ecosystem_config.get_management(management)

    managed_subroutines = ecosystem_config.get_subroutines_enabled()
    managed_subroutines.sort()
    subroutines_list = subroutine_names.copy()
    subroutines_list.sort()
    assert managed_subroutines == subroutines_list


def test_ecosystem_chaos(ecosystem_config: EcosystemConfig):
    today = datetime.now(timezone.utc).replace(
        hour=14, minute=0, second=0, microsecond=0)

    assert ecosystem_config.chaos_config == gv.ChaosConfig()

    with pytest.raises(ValueError):
        ecosystem_config.chaos_config = {"wrong": "value"}

    max_intensity = 1.2
    duration = 2
    parameters = {"frequency": 1, "duration": duration, "intensity": max_intensity}
    ecosystem_config.chaos_config = parameters
    assert ecosystem_config.chaos_config == gv.ChaosConfig(**parameters)

    # By default, the newly created cfg has empty chaos time window
    chaos_time_window = ecosystem_config.chaos_time_window
    assert chaos_time_window["beginning"] is None
    assert chaos_time_window["end"] is None
    assert ecosystem_config.get_chaos_factor() == 1.0

    # Update the time window. It will automatically update as the frequency is 1
    chaos_memory = ecosystem_config.general.get_chaos_memory(ecosystem_config.uid)
    chaos_memory["last_update"] = date(2000, 1, 1)  # Allow to update
    ecosystem_config.update_chaos_time_window()
    chaos_time_window = ecosystem_config.chaos_time_window
    assert chaos_time_window["beginning"] == today
    assert chaos_time_window["end"] == today + timedelta(days=duration)

    ecosystem_config.update_chaos_time_window()
    with get_logs_content(ecosystem_config.general.logs_dir / "gaia.log") as logs:
        assert "Chaos time window is already up to date." in logs

    chaos_factor = ecosystem_config.get_chaos_factor(today + timedelta(days=1))
    # chaos_factor should be at its maximum
    assert chaos_factor == max_intensity


def test_ecosystem_light_method(ecosystem_config: EcosystemConfig):
    assert ecosystem_config.light_method is gv.LightMethod.fixed
    new_method = gv.LightMethod.elongate

    with pytest.raises(ValueError):
        ecosystem_config.set_light_method(new_method)

    ecosystem_config.general.home_coordinates = (0, 0)
    ecosystem_config.set_light_method(new_method)

    # Should not happen
    ecosystem_config.general._sun_times = {}
    ecosystem_config.general.app_config.TESTING = False
    # Sun times is none so `light_method` falls back to `fixed`
    assert ecosystem_config.light_method is gv.LightMethod.fixed
    ecosystem_config.general.app_config.TESTING = True
    ecosystem_config.general._sun_times = {
        "home": {"last_update": date.today(), "data": sun_times}
    }
    assert ecosystem_config.light_method is new_method


def test_ecosystem_climate_parameters(ecosystem_config: EcosystemConfig):
    with pytest.raises(UndefinedParameter):
        ecosystem_config.get_climate_parameter("temperature")
    with pytest.raises(ValueError):
        ecosystem_config.set_climate_parameter("temperature", **{"wrong": "value"})

    parameters = {"day": 25, "night": 20, "hysteresis": 1}
    ecosystem_config.set_climate_parameter("temperature", **parameters)
    assert ecosystem_config.get_climate_parameter("temperature") == \
           gv.ClimateConfig(parameter="temperature", **parameters)

    ecosystem_config.delete_climate_parameter("temperature")
    with pytest.raises(UndefinedParameter):
        ecosystem_config.delete_climate_parameter("temperature")


def test_ecosystem_time_parameters(ecosystem_config: EcosystemConfig):
    assert ecosystem_config.time_parameters == gv.DayConfig()

    with pytest.raises(ValueError):
        ecosystem_config.time_parameters = {"wrong": "value"}

    ecosystem_config.time_parameters = {"day": "4h21", "night": "22h00"}
    assert ecosystem_config.time_parameters.day == time(4, 21)
