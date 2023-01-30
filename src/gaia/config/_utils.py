from pathlib import Path
import sys
from typing import Type

from gaia.config.base import BaseConfig, DIR


class GaiaConfig(BaseConfig):
    pass


_state: dict = {
    "base_dir": None,
    "config": None,
}


class AppInfo:
    APP_NAME = "Gaia"
    VERSION = "0.5.3"


def get_base_dir() -> Path:
    global _state
    if _state["base_dir"] is None:
        _state["base_dir"] = Path(DIR)
        if not _state["base_dir"].exists():
            raise ValueError(
                "Environment variable `OURANOS_DIR` is not set to a valid path"
            )
    return _state["base_dir"]


def _get_config() -> Type[GaiaConfig]:
    base_dir = get_base_dir()
    sys.path.extend([str(base_dir)])
    try:
        from config import Config
    except ImportError:

        class GaiaConfig(AppInfo, BaseConfig):
            pass
        return GaiaConfig

    else:
        if not issubclass(Config, BaseConfig):
            raise RuntimeError(
                "Your custom config should be a subclass of "
                "'gaia.config.BaseConfig'"
            )

        class GaiaConfig(AppInfo, Config):
            pass

        return GaiaConfig


def get_config() -> Type[GaiaConfig]:
    global _state
    if _state["config"] is None:
        _state["config"] = _get_config()
    return _state["config"]
