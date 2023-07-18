from pathlib import Path
import sys
from typing import Type
import warnings

from gaia import __version__ as version
from gaia.config.base import BaseConfig, DIR


_state: dict = {
    "base_dir": None,
    "config": None,
}


class AppInfo:
    APP_NAME = "Gaia"
    VERSION = version


class GaiaConfig(AppInfo, BaseConfig):
    pass


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
    sys.path.insert(0, str(base_dir))
    try:
        from config import Config
    except ImportError:

        class GaiaConfig(AppInfo, BaseConfig):
            pass
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


def _get_dir(name: str, fallback_path: str) -> Path:
    config: Type[GaiaConfig] = get_config()
    path = getattr(config, name)
    try:
        dir_ = Path(path)
    except ValueError:
        warnings.warn(
            f"The dir specified by {name} is not valid, using fallback path "
            f"{fallback_path}"
        )
        base_dir = get_base_dir()
        dir_ = base_dir / fallback_path
    if not dir_.exists():
        dir_.mkdir(parents=True)
    return dir_


def get_cache_dir() -> Path:
    return _get_dir("CACHE_DIR", ".cache")


def get_log_dir() -> Path:
    return _get_dir("LOG_DIR", ".logs")
