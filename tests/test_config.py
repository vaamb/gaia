from src.config_parser import GeneralConfig


def test_general_config_singleton(temp_dir, general_config):
    assert general_config is GeneralConfig(temp_dir)


def test_config_files_created(general_config):
    for cfg in ("ecosystems", "private"):
        cfg_file = general_config._base_dir/f"{cfg}.cfg"
        assert cfg_file.is_file()


def test_config_files_watchdog(general_config):
    general_config.start_watchdog()
    for cfg in general_config._hash_dict:
        assert(isinstance(general_config._hash_dict[cfg], str))
    general_config.stop_watchdog()
