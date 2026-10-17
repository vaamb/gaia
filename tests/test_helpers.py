from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from gaia.config.from_files import EngineConfig
from gaia.hardware import get_hardware_specs
from gaia.helpers import dump_hardware_specs, validate_configs
from gaia.utils import json


def test_validate_configs_checks_requirements_by_default(engine_config: EngineConfig):
    # `load` is mocked out: the singleton's `config_files_lock` is already bound
    # to the test event loop, but the CLI runs its own loop via `asyncio.run`.
    with (
        patch.object(EngineConfig, "load") as mock_load,
        patch.object(EngineConfig, "_check_hardware_requirements") as mock_check,
    ):
        result = CliRunner().invoke(validate_configs, catch_exceptions=False)

    assert result.exit_code == 0
    mock_load.assert_awaited()
    mock_check.assert_awaited_once()


def test_validate_configs_skips_requirements_when_flag_set(engine_config: EngineConfig):
    # `--check-requirements`/`-r` is a flag with `default=True`, so passing it
    # toggles the value to False, disabling the requirements check.
    with (
        patch.object(EngineConfig, "load") as mock_load,
        patch.object(EngineConfig, "_check_hardware_requirements") as mock_check,
    ):
        result = CliRunner().invoke(
            validate_configs, ["-r"], catch_exceptions=False)

    assert result.exit_code == 0
    mock_load.assert_awaited()
    mock_check.assert_not_awaited()


def test_dump_hardware_specs(tmp_path: Path):
    file_path = tmp_path / "hardware_specs.json"
    result = CliRunner().invoke(
        dump_hardware_specs, [str(file_path)], catch_exceptions=False)

    assert result.exit_code == 0
    assert json.loads(file_path.read_text()) == get_hardware_specs()
