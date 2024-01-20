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
    def __init__(self) -> None:
        raise ValueError("'GaiaConfig' should only be used for type hint purposes.")


class GaiaConfigHelper:
    _config: GaiaConfig | None = None

    @classmethod
    def _find_app_config_cls(cls) -> Type[BaseConfig]:
        logger = logging.getLogger("gaia.config_helper")
        lookup_dir = os.environ.get("GAIA_DIR")
        if lookup_dir is not None:
            logger.info("Trying to get GaiaConfig from 'GAIA_DIR'.")
        else:
            logger.info("Trying to get GaiaConfig from current directory.")
            lookup_dir = os.getcwd()

        sys.path.insert(0, str(lookup_dir))
        try:
            from config import Config
        except ImportError:
            return BaseConfig
        else:
            if not issubclass(Config, BaseConfig):
                raise ValueError(
                    "Your custom config should be a subclass of "
                    "'gaia.config.BaseConfig'."
                )
            return Config

    @classmethod
    def config_is_set(cls) -> None:
        return cls._config is not None

    @classmethod
    def get_config(cls) -> GaiaConfig:
        if not cls.config_is_set():
            config: Type[BaseConfig] = cls._find_app_config_cls()
            cls.set_config(config)
        return cls._config

    @classmethod
    def set_config(cls, config_cls: Type[BaseConfig]) -> GaiaConfig:
        if cls._config is not None:
            raise RuntimeError("Config has already been set.")
        if not issubclass(config_cls, BaseConfig):
            raise ValueError(
                "Your custom config should be a subclass of "
                "'gaia.config.BaseConfig'."
            )

        class Config(AppInfo, config_cls):
            pass

        cls._config = Config()
        return cls._config

    @classmethod
    def reset_config(cls) -> None:
        if not cls.config_is_set():
            raise ValueError("Cannot reset a non-set config.")
        if not cls._config.TESTING:
            raise ValueError("Only testing config can be reset.")
        cls._config = None


def configure_logging(config_class: GaiaConfig) -> None:
    testing = config_class.TESTING
    debug = config_class.DEBUG or testing
    log_to_stdout = config_class.LOG_TO_STDOUT
    log_to_file = config_class.LOG_TO_FILE
    log_error = config_class.LOG_ERROR

    log_dir = Path(config_class.LOG_DIR)
    if not log_dir.exists():
        log_dir.mkdir(parents=True)

    base_level = 'DEBUG' if debug else 'INFO'

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
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "fileFormat": {
                "format": "%(asctime)s -- %(levelname)-7.7s  -- %(name)s -- %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "testingFormat": {
                "format": "%(message)s",
            },
        },

        "handlers": {
            "streamHandler": {
                "level": base_level,
                "formatter": "streamFormat",
                "class": "logging.StreamHandler",
            },
            "fileHandler": {
                "level": base_level,
                "formatter": "fileFormat" if not testing else "testingFormat",
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
                "handlers": "",
                "level": base_level,
            },
            "gaia": {
                "handlers": handlers,
                "level": base_level,
            },
            "apscheduler": {
                "handlers": handlers,
                "level": f"{'DEBUG' if debug else 'WARNING'}",
            },
            "engineio": {
                "handlers": handlers,
                "level": f"{'DEBUG' if debug else 'INFO'}",
            },
            "dispatcher": {
                "handlers": handlers,
                "level": f"{'DEBUG' if debug else 'WARNING'}",
            },
            "urllib3": {
                "handlers": handlers,
                "level": "WARNING",
            },
            "virtual" : {
                "handlers": ["streamHandler"] if config_class.DEVELOPMENT and log_to_stdout else "",
                "level": "INFO",
            }
        },
    }
    logging.config.dictConfig(logging_config)
