from pathlib import Path
import re
from unittest import TestCase

from gaia import __version__


def _get_pattern(script_path: Path, pattern: re.Pattern) -> str:
    with open(script_path, "r") as f:
        script_text = f.read()

    search = pattern.search(script_text)
    if search is not None:
        return search.group(0)
    raise ValueError(f"Pattern {pattern} not found in {script_path}")


class TestInstallScript(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root_dir = Path(__file__).parents[1]
        cls.pyproject = cls.root_dir / "pyproject.toml"
        cls.scripts_dir = cls.root_dir / "scripts"
        cls.install_script_path = cls.scripts_dir / "install.sh"
        cls.logging_script_path = cls.scripts_dir / "logging.sh"

    def test_gaia_version(self):
        # Sync the version between gaia and install.sh
        pattern = re.compile(r"(?<=GAIA_VERSION=\")(.+?)(?=\"\n)", re.DOTALL)
        gaia_version = _get_pattern(self.install_script_path, pattern)
        assert gaia_version == __version__

    def test_python_version(self):
        pattern = re.compile(r"(?<=MIN_PYTHON_VERSION=\")(.+?)(?=\"\n)", re.DOTALL)
        install_version = _get_pattern(self.install_script_path, pattern)

        pattern = re.compile(r"(?<=requires-python = \")(.+?)(?=\"\n)", re.DOTALL)
        toml_version = _get_pattern(self.pyproject, pattern)
        assert toml_version[:2] == ">="
        toml_version = toml_version[2:]

        assert install_version == toml_version

    def test_logging_sync(self):
        pattern = re.compile(r"(?<=#>>>Logging>>>)(.*)(?=#<<<Logging<<<)", re.DOTALL)

        install_code = _get_pattern(self.install_script_path, pattern)
        logging_code = _get_pattern(self.logging_script_path, pattern)

        assert install_code == logging_code
