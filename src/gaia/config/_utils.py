from __future__ import annotations

import logging
import logging.config
from pathlib import Path
import sys
from typing import Type
import warnings

from gaia import __version__ as version
from gaia.config.base import BaseConfig, DIR


class AppInfo:
    APP_NAME = "Gaia"
    VERSION = version


class GaiaConfig(AppInfo, BaseConfig):
    pass


_base_dir: Path | None = None
_config: Type[GaiaConfig] | None = None


def get_base_dir() -> Path:
    global _base_dir
    if _base_dir is None:
        _base_dir = Path(DIR)
        if not _base_dir.exists():
            raise ValueError(
                "Environment variable `GAIA_DIR` is not set to a valid path"
            )
    return _base_dir


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
            raise ValueError(
                "Your custom config should be a subclass of "
                "'gaia.config.BaseConfig'"
            )

        class GaiaConfig(AppInfo, Config):
            pass
    return GaiaConfig


def get_config() -> Type[GaiaConfig]:
    global _config
    if _config is None:
        _config = _get_config()
    return _config


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


def configure_logging(config_class: Type[GaiaConfig]):
    DEBUG = config_class.DEBUG
    LOG_TO_STDOUT = config_class.LOG_TO_STDOUT
    LOG_TO_FILE = config_class.LOG_TO_FILE
    LOG_ERROR = config_class.LOG_ERROR

    handlers = []

    if LOG_TO_STDOUT:
        handlers.append("streamHandler")

    if LOG_TO_FILE:
        handlers.append("fileHandler")

    if LOG_ERROR:
        handlers.append("errorFileHandler")

    LOGGING_CONFIG = {
        "version": 1,
        "disable_existing_loggers": False,

        "formatters": {
            "streamFormat": {
                "format": (
                    "%(asctime)s %(levelname)-4.4s [%(filename)-20.20s:%(lineno)3d] %(name)-35.35s: %(message)s"
                    if DEBUG else
                    "%(asctime)s %(levelname)-4.4s %(name)-35.35s: %(message)s"
                ),
                "datefmt": "%Y-%m-%d %H:%M:%S"
            },
            "fileFormat": {
                "format": "%(asctime)s -- %(levelname)-7.7s  -- %(name)s -- %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S"
            },
        },

        "handlers": {
            "streamHandler": {
                "level": f"{'DEBUG' if DEBUG else 'INFO'}",
                "formatter": "streamFormat",
                "class": "logging.StreamHandler",
            },
            "fileHandler": {
                "level": f"{'DEBUG' if DEBUG else 'INFO'}",
                "formatter": "fileFormat",
                "class": "logging.handlers.RotatingFileHandler",
                'filename': f"{get_log_dir()/'base.log'}",
                "mode": "w+",
                "maxBytes": 1024 * 512,
                "backupCount": 5,
            },
            "errorFileHandler": {
                "level": "ERROR",
                "formatter": "fileFormat",
                "class": "logging.FileHandler",
                "filename": f"{get_log_dir()/'errors.log'}",
                "mode": "a",
            }
        },

        "loggers": {
            "": {
                "handlers": handlers,
                "level": f"{'DEBUG' if DEBUG else 'INFO'}"
            },
            "apscheduler": {
                "handlers": handlers,
                "level": f"{'DEBUG' if DEBUG else 'WARNING'}"
            },
            "engineio": {
                "handlers": handlers,
                "level": f"{'DEBUG' if DEBUG else 'INFO'}"
            },
            "dispatcher": {
                "handlers": handlers,
                "level": f"{'DEBUG' if DEBUG else 'WARNING'}"
            },
            "urllib3": {
                "handlers": handlers,
                "level": "WARNING"
            },
        },
    }
    logging.config.dictConfig(LOGGING_CONFIG)
