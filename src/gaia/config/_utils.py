from __future__ import annotations

import logging
import logging.config
import os
from pathlib import Path
import sys
from typing import Type

from gaia import __version__ as version
from gaia.config.base import BaseConfig


class AppInfo:
    APP_NAME = "Gaia"
    VERSION = version


class GaiaConfig(AppInfo, BaseConfig):
    pass


_config: Type[GaiaConfig] | None = None
_lookup_dir = os.environ.get("GAIA_DIR") or os.getcwd()


def _get_config() -> Type[GaiaConfig]:
    sys.path.insert(0, str(_lookup_dir))
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


def set_config(config: Type[GaiaConfig]) -> None:
    global _config
    if _config is not None:
        raise RuntimeError(
            "'set_config' cannot be called once 'get_config' is called")
    _config = config


def configure_logging(config_class: Type[GaiaConfig]):
    debug = config_class.DEBUG
    log_to_stdout = config_class.LOG_TO_STDOUT
    log_to_file = config_class.LOG_TO_FILE
    log_error = config_class.LOG_ERROR

    log_dir = Path(config_class.LOG_DIR)

    handlers = []

    if log_to_stdout:
        handlers.append("streamHandler")

    if log_to_file:
        handlers.append("fileHandler")

    if log_error:
        handlers.append("errorFileHandler")

    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,

        "formatters": {
            "streamFormat": {
                "format": (
                    "%(asctime)s %(levelname)-4.4s [%(filename)-20.20s:%(lineno)3d] %(name)-35.35s: %(message)s"
                    if debug else
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
                "level": f"{'DEBUG' if debug else 'INFO'}",
                "formatter": "streamFormat",
                "class": "logging.StreamHandler",
            },
            "fileHandler": {
                "level": f"{'DEBUG' if debug else 'INFO'}",
                "formatter": "fileFormat",
                "class": "logging.handlers.RotatingFileHandler",
                'filename': f"{log_dir/'base.log'}",
                "mode": "w+",
                "maxBytes": 1024 * 512,
                "backupCount": 5,
            },
            "errorFileHandler": {
                "level": "ERROR",
                "formatter": "fileFormat",
                "class": "logging.FileHandler",
                "filename": f"{log_dir/'errors.log'}",
                "mode": "a",
            }
        },

        "loggers": {
            "": {
                "handlers": handlers,
                "level": f"{'DEBUG' if debug else 'INFO'}"
            },
            "apscheduler": {
                "handlers": handlers,
                "level": f"{'DEBUG' if debug else 'WARNING'}"
            },
            "engineio": {
                "handlers": handlers,
                "level": f"{'DEBUG' if debug else 'INFO'}"
            },
            "dispatcher": {
                "handlers": handlers,
                "level": f"{'DEBUG' if debug else 'WARNING'}"
            },
            "urllib3": {
                "handlers": handlers,
                "level": "WARNING"
            },
        },
    }
    logging.config.dictConfig(logging_config)
