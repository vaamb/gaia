from asyncio import sleep
from datetime import date, datetime, time, timedelta, timezone

import pytest

import gaia_validators as gv

from gaia.config import ConfigType, EcosystemConfig, EngineConfig
from gaia.exceptions import HardwareNotFound, UndefinedParameter
from gaia.subroutines import subroutine_names
from gaia.utils import get_yaml

from .data import (
    ecosystem_info, ecosystem_name, humidifier_info, humidifier_uid,
    lighting_method, sensor_info, sensor_uid, sun_times)


# ---------------------------------------------------------------------------
#   Test EngineConfig
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
class TestEngineConfig:
    def test_singleton(self, engine_config: EngineConfig):
        assert engine_config is EngineConfig()

    def test_initialization(self, engine_config: EngineConfig):
        for cfg_type in ConfigType:
            cfg_path = engine_config.get_file_path(cfg_type)
            assert cfg_path.exists()
            assert cfg_path.is_file()

    def test_ecosystem_config_creation_deletion(self, engine_config: EngineConfig):
        engine_config.create_ecosystem("Already fading away")
        engine_config.delete_ecosystem("Already fading away")
        with pytest.raises(ValueError):
            engine_config.delete_ecosystem("Already fading away")

    async def test_save_and_load(self, engine_config: EngineConfig):
        ecosystems_cfg = engine_config.ecosystems_config
        private_config = engine_config.private_config
        for cfg_type in ConfigType:
            await engine_config.save(cfg_type)
            await engine_config.load(cfg_type)
        assert engine_config.ecosystems_config == ecosystems_cfg
        assert engine_config.private_config == private_config

    def test_refresh_suntimes(
            self,
            engine_config: EngineConfig,
            ecosystem_config: EcosystemConfig,
            logs_content,
    ):
        assert engine_config.home_sun_times is None
        ecosystem_config.nycthemeral_cycle["lighting"] = gv.LightMethod.elongate
        engine_config.home_coordinates = (0, 0)
        engine_config.refresh_sun_times()
        with logs_content() as logs:
            assert "have been refreshed" in logs
            assert "Failed to refresh" not in logs
        assert engine_config.home_sun_times is not None

    def test_status(self, engine_config: EngineConfig, ecosystem_config: EcosystemConfig):
        assert engine_config.ecosystems_name == [ecosystem_name]
        assert engine_config.get_ecosystems_expected_to_run() == set()
        ecosystem_config.status = True
        assert engine_config.get_ecosystems_expected_to_run() == {ecosystem_config.uid}

    def test_get_IDs(self, engine_config: EngineConfig, ecosystem_config: EcosystemConfig):
        assert engine_config.get_IDs(ecosystem_name).uid == ecosystem_config.uid
        assert engine_config.get_IDs(ecosystem_name).name == ecosystem_config.name
        with pytest.raises(ValueError):
            engine_config.get_IDs("not in config")

    def test_home(self, engine_config: EngineConfig):
        with pytest.raises(UndefinedParameter):
            engine_config.home_coordinates
        engine_config.home_coordinates = (4, 2)
        assert engine_config.home_coordinates.latitude == 4.0
        assert engine_config.home_coordinates.longitude == 2.0


@pytest.mark.asyncio
class TestWatchdog:
    pytest.mark.asyncio(loop_scope="function")
    async def test_config_files_watchdog(self, engine_config: EngineConfig, logs_content):
        yaml = get_yaml()

        # Start watchdog and make sure it can only be started once
        engine_config.watchdog.start()
        with logs_content() as logs:
            assert "Starting the configuration files watchdog" in logs
        with pytest.raises(RuntimeError):
            engine_config.watchdog.start()

        # Test watchdog for ecosystems cfg
        engine_config.create_ecosystem("Already fading away")
        with open(engine_config.config_dir / ConfigType.ecosystems.value, "w") as cfg:
            yaml.dump(engine_config.ecosystems_config_dict, cfg)
        await sleep(0.15)  # Allow to check at least once if files changed. Rem: watcher period set to 0.10 sec
        with logs_content() as logs:
            assert "Change in ecosystems configuration file detected" in logs

        # Test watchdog for private cfg
        engine_config.set_place(place="Nowhere", coordinates=(0.0, 0.0))
        with open(engine_config.config_dir / ConfigType.private.value, "w") as cfg:
            yaml.dump(engine_config.private_config, cfg)
        await sleep(0.15)  # Allow to check at least once if files changed. Rem: watcher period set to 0.10 sec
        with logs_content() as logs:
            assert "Change in private configuration file detected" in logs

        # Stop watchdog and make sure it can only be stopped once
        engine_config.watchdog.stop()
        with logs_content() as logs:
            assert "Stopping the configuration files watchdog" in logs
        with pytest.raises(RuntimeError):
            engine_config.watchdog.stop()

        # Restore config files
        with open(engine_config.config_dir / ConfigType.ecosystems.value, "w") as cfg:
            yaml.dump(ecosystem_info, cfg)
        with open(engine_config.config_dir / ConfigType.private.value, "w") as cfg:
            yaml.dump({}, cfg)


# ---------------------------------------------------------------------------
#   Test EcosystemConfig
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
class TestEcosystemConfigGeneral:
    def test_singleton(self, ecosystem_config: EcosystemConfig):
        assert ecosystem_config is EcosystemConfig(ecosystem_name)

    def test_dict(
            self,
            engine_config: EngineConfig,
            ecosystem_config: EcosystemConfig,
    ):
        assert ecosystem_config.general.__dict__ is engine_config.__dict__
        assert (
            ecosystem_config._config_dict
            is engine_config.ecosystems_config_dict[ecosystem_config.uid]
        )

    def test_name(self, ecosystem_config: EcosystemConfig):
        assert ecosystem_config.name == ecosystem_name
        ecosystem_config.name = "name"
        assert ecosystem_config.name == "name"

    def test_status(self, ecosystem_config: EcosystemConfig):
        assert ecosystem_config.status is False
        ecosystem_config.status = True
        assert ecosystem_config.status is True

    def test_managed_subroutines(self, ecosystem_config: EcosystemConfig):
        assert not ecosystem_config.get_subroutines_enabled()

        for management in gv.ManagementFlags:
            ecosystem_config.set_management(management, True)
            assert ecosystem_config.get_management(management)

        managed_subroutines = ecosystem_config.get_subroutines_enabled()
        managed_subroutines.sort()
        subroutines_list = subroutine_names.copy()
        subroutines_list.sort()
        assert managed_subroutines == subroutines_list


@pytest.mark.asyncio
class TestEcosystemConfigClimate:
    async def test_chaos(self, ecosystem_config: EcosystemConfig, logs_content):
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
        await ecosystem_config.update_chaos_time_window()
        chaos_time_window = ecosystem_config.chaos_time_window
        assert chaos_time_window["beginning"] == today
        assert chaos_time_window["end"] == today + timedelta(days=duration)

        await ecosystem_config.update_chaos_time_window()
        with logs_content() as logs:
            assert "Chaos time window is already up to date." in logs

        chaos_factor = ecosystem_config.get_chaos_factor(today + timedelta(days=1))
        # chaos_factor should be at its maximum
        assert chaos_factor == max_intensity

    async def test_light_method(self, ecosystem_config: EcosystemConfig):
        assert ecosystem_config.lighting_method is lighting_method
        new_method = gv.LightMethod.elongate

        with pytest.raises(ValueError, match="coordinates of 'home' are not provided"):
            await ecosystem_config.set_lighting_method(new_method)

        ecosystem_config.general.home_coordinates = (0, 0)
        await ecosystem_config.set_lighting_method(new_method)

        # Should not happen
        del ecosystem_config.general.places["home"]
        ecosystem_config.general._sun_times = {}
        ecosystem_config.general.app_config.TESTING = False
        # Reset caches after this big change in config
        ecosystem_config.reset_caches()
        # Sun times is none so `light_method` falls back to `fixed`
        assert ecosystem_config.lighting_method is gv.LightMethod.fixed
        ecosystem_config.general.app_config.TESTING = True
        # Reset caches after this big change in config
        ecosystem_config.reset_caches()
        ecosystem_config.general._sun_times = {
            "home": {"last_update": date.today(), "data": sun_times}
        }
        assert ecosystem_config.lighting_method is new_method

    async def test_nycthemeral_span(self, ecosystem_config: EcosystemConfig):
        assert ecosystem_config.nycthemeral_span_hours == gv.NycthemeralSpanConfig()

        # Test span hours
        with pytest.raises(ValueError, match="Invalid time parameters provided"):
            await ecosystem_config.set_nycthemeral_span_hours({"wrong": "value"})

        await ecosystem_config.set_nycthemeral_span_hours({"day": "4h21", "night": "22h00"})
        assert ecosystem_config.nycthemeral_span_hours.day == time(4, 21)
        assert ecosystem_config.nycthemeral_span_hours.night == time(22, 00)

        # Test span method
        with pytest.raises(ValueError, match="is not a valid"):
            await ecosystem_config.set_nycthemeral_span_method("wrong_value")

        # Fixed method should always work
        await ecosystem_config.set_nycthemeral_span_method(gv.NycthemeralSpanMethod.fixed)

        # Mimic is more tedious
        with pytest.raises(
                ValueError,
                match="no target is specified in the ecosystems configuration file"
        ):
            await ecosystem_config.set_nycthemeral_span_method(
                gv.NycthemeralSpanMethod.mimic)

        with pytest.raises(ValueError, match="The place targeted must first be set with"):
            await ecosystem_config.set_nycthemeral_span_target("span_target")

        ecosystem_config.general.set_place("span_target", (42.618, 21.1415))
        await ecosystem_config.set_nycthemeral_span_target("span_target")

        await ecosystem_config.set_nycthemeral_span_method(gv.NycthemeralSpanMethod.mimic)

        assert isinstance(
            ecosystem_config.nycthemeral_span_method, gv.NycthemeralSpanMethod)

        # Test setting the whole cycle
        await ecosystem_config.set_nycthemeral_cycle(
            span="fixed", lighting="fixed", target=None, day="8h42", night="21h00")
        assert ecosystem_config.nycthemeral_span_method == gv.NycthemeralSpanMethod.fixed
        assert ecosystem_config.lighting_method == gv.LightMethod.fixed
        assert ecosystem_config.nycthemeral_span_target is None
        assert ecosystem_config.nycthemeral_span_hours.day == time(8, 42)
        assert ecosystem_config.nycthemeral_span_hours.night == time(21, 00)

    def test_climate_parameters(self, ecosystem_config: EcosystemConfig):
        with pytest.raises(UndefinedParameter):
            ecosystem_config.get_climate_parameter("light")
        with pytest.raises(ValueError):
            ecosystem_config.set_climate_parameter("light", wrong="value")

        parameters = {"day": 250000, "night": 0, "hysteresis": 10000}
        ecosystem_config.set_climate_parameter("light", **parameters)
        assert ecosystem_config.get_climate_parameter("light") == gv.ClimateConfig(
            parameter="light", **parameters)

        ecosystem_config.delete_climate_parameter("light")
        with pytest.raises(UndefinedParameter):
            ecosystem_config.delete_climate_parameter("light")

    def test_actuator_couples(self, ecosystem_config: EcosystemConfig):
        actuator_couples = ecosystem_config.get_actuator_couples()

        # Test with a parameter that has an actuator override
        actuator_couple = actuator_couples[gv.ClimateParameter.humidity]
        assert actuator_couple.increase == "fogger"
        assert actuator_couple.decrease == "dehumidifier"

        # Test with a parameter that uses default actuators
        actuator_couple = actuator_couples[gv.ClimateParameter.wind]
        assert actuator_couple.increase == "fan"
        assert actuator_couple.decrease is None

    def test_valid_actuator_groups(self, ecosystem_config: EcosystemConfig):
        valid_actuator_groups = ecosystem_config.get_valid_actuator_groups()

        assert valid_actuator_groups == {
            # Overridden
            "fogger", "rainer",
            # Default
            "heater", "cooler", "dehumidifier", "light", "fan",
        }


class TestEcosystemConfigHardware:
    def test_create_fail_address(self, ecosystem_config: EcosystemConfig):
        with pytest.raises(ValueError, match=r"Address .* already used"):
            ecosystem_config.create_new_hardware(**sensor_info)

    def test_create_fail_model(self, ecosystem_config: EcosystemConfig):
        invalid_hardware_info = {
            **sensor_info,
            "address": "GPIO_11",  # Use a free address
            "model": "Invalid",
        }
        with pytest.raises(ValueError, match="This hardware model is not supported"):
            ecosystem_config.create_new_hardware(**invalid_hardware_info)

    def test_create_fail_type(self, ecosystem_config: EcosystemConfig):
        invalid_hardware_info = {
            **sensor_info,
            "address": "GPIO_7",  # Use a free address
            "type": "Invalid",
        }
        error_msg = "VALUE ERROR at parameter 'type', input 'Invalid' is not valid"
        with pytest.raises(ValueError, match=error_msg):
            ecosystem_config.create_new_hardware(**invalid_hardware_info)

    def test_create_fail_level(self, ecosystem_config: EcosystemConfig):
        invalid_hardware_info = {
            **sensor_info,
            "address": "GPIO_7",  # Use a free address
            "level": "Invalid",
        }
        error_msg = "VALUE ERROR at parameter 'level', input 'Invalid' is not valid"
        with pytest.raises(ValueError, match=error_msg):
            ecosystem_config.create_new_hardware(**invalid_hardware_info)

    def test_create_success(self, ecosystem_config: EcosystemConfig):
        valid_hardware_info = {
            **humidifier_info,
            "model": "gpioSwitch",
            "address": "GPIO_11",  # Use a free address
        }
        ecosystem_config.create_new_hardware(**valid_hardware_info)

    def test_update_fail_not_found(self, ecosystem_config: EcosystemConfig):
        with pytest.raises(HardwareNotFound):
            ecosystem_config.update_hardware("invalid_uid", address="GPIO_7")

    def test_update_fail_address(self, ecosystem_config: EcosystemConfig):
        with pytest.raises(ValueError, match=r"Address .* already used"):
            hardware_address = sensor_info["address"]
            ecosystem_config.update_hardware(humidifier_uid, address=hardware_address)

    def test_update_fail_model(self, ecosystem_config: EcosystemConfig):
        with pytest.raises(ValueError, match="This hardware model is not supported"):
            ecosystem_config.update_hardware(humidifier_uid, model="Invalid")

    def test_update_fail_type(self, ecosystem_config: EcosystemConfig):
        error_msg = "VALUE ERROR at parameter 'type', input 'Invalid' is not valid"
        with pytest.raises(ValueError, match=error_msg):
            ecosystem_config.update_hardware(humidifier_uid, type="Invalid")

    def test_update_fail_level(self, ecosystem_config: EcosystemConfig):
        error_msg = "VALUE ERROR at parameter 'level', input 'Invalid' is not valid"
        with pytest.raises(ValueError, match=error_msg):
            ecosystem_config.update_hardware(humidifier_uid, level="Invalid")

    def test_update_success(self, ecosystem_config: EcosystemConfig):
        ecosystem_config.update_hardware(humidifier_uid, model="gpioSwitch", address="BOARD_37")

    def test_delete_fail_not_found(self, ecosystem_config: EcosystemConfig):
        with pytest.raises(HardwareNotFound):
            ecosystem_config.delete_hardware("invalid_uid")

    def test_hardware_delete_success(self, ecosystem_config: EcosystemConfig):
        ecosystem_config.delete_hardware(sensor_uid)
