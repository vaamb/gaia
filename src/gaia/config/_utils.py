from __future__ import annotations

import logging
import logging.config
import os
from pathlib import Path
import sys
from typing import Any, Type
import warnings

from gaia import __version__ as version
from gaia.config.base import BaseConfig


class AppInfo:
    APP_NAME = "Gaia"
    VERSION = version


class GetPathMixin:
    def __init__(self):
        self._paths: dict[str, Path] = {}

    def get_path(self, dir_name: str) -> Path:
        try:
            return self._paths[dir_name]
        except KeyError:
            try:
                path = Path(getattr(self, dir_name))
            except ValueError:
                raise ValueError(f"Config.{dir_name} is not a valid directory.")
            else:
                if not path.exists():
                    warnings.warn(
                        f"'Config.{dir_name}' variable is set to a non-existing "
                        f"directory, trying to create it.")
                    path.mkdir(parents=True)
                self._paths[dir_name] = path
                return path


class GaiaConfig(AppInfo, BaseConfig, GetPathMixin):
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
            # Valid ignore: `config` is found at runtime
            from config import Config  # ty: ignore[unresolved-import]
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
    def config_is_set(cls) -> bool:
        return cls._config is not None

    @classmethod
    def get_config(cls) -> GaiaConfig:
        if not cls.config_is_set():
            config: Type[BaseConfig] = cls._find_app_config_cls()
            cls.set_config(config)
        # Type narrowing as `ty` doesn't see through `set_config()`
        assert cls._config is not None
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

        class Config(AppInfo, GetPathMixin, config_cls):  # ty: ignore[unsupported-base]
            pass

        cls._config = Config()
        return cls._config

    @classmethod
    def reset_config(cls) -> None:
        if cls._config is None:
            raise ValueError("Cannot reset a non-set config.")
        if not cls._config.TESTING:
            raise ValueError("Only testing config can be reset.")
        cls._config = None


handlers: list[str] = []

base_fmt = "%(asctime)s %(levelname)-7.7s: %(name)-35.35s: %(message)s"


logging_config: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "base_formatter": {
            "format": base_fmt,
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "file_formatter": {
            "format": base_fmt,
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "stream_handler": {
            "level": "INFO",
            "formatter": "base_formatter",
            "class": "logging.StreamHandler",
        },
        "file_handler": {
            "level": "INFO",
            "formatter": "file_formatter",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "gaia.log",
            "mode": "a",
            "maxBytes": 4 * 1024 * 1024,
            "backupCount": 5,
        },
    },
    "loggers": {
        "gaia": {
            "handlers": handlers,
            "level": "INFO",
        },
        "virtual": {
            "handlers": "",
            "level": "INFO",
        },
        "dispatcher": {
            "handlers": handlers,
            "level": "WARNING",
        },
        "apscheduler": {
            "handlers": handlers,
            "level": "WARNING",
        },
    },
}


def configure_logging(config_class: GaiaConfig) -> None:
    # Create the log dir if it doesn't exist
    log_dir = Path(config_class.LOG_DIR)
    if not log_dir.exists():
        log_dir.mkdir(parents=True)

    if config_class.DEBUG or config_class.TESTING:
        debug_fmt = "%(asctime)s %(levelname)-7.7s: [%(filename)-20.20s:%(lineno)4d] %(name)-35.35s: %(message)s"
        logging_config["formatters"]["base_formatter"]["format"] = debug_fmt
        file_debug_fmt = (
            "%(asctime)s\t%(levelno)d\t%(levelname)s\t%(name)s\t%(filename)s\t%(lineno)d\t%(funcName)s\t%(msg)s")
        logging_config["formatters"]["file_formatter"]["format"] = file_debug_fmt
        logging_config["handlers"]["stream_handler"]["level"] = "DEBUG"
        logging_config["handlers"]["file_handler"]["level"] = "DEBUG"
        logging_config["handlers"]["file_handler"]["filename"] = "gaia.debug.log"
        logging_config["loggers"]["gaia"]["level"] = "DEBUG"
        logging_config["loggers"]["virtual"]["level"] = "DEBUG"
        logging_config["loggers"]["dispatcher"]["level"] = "DEBUG"
        logging_config["loggers"]["apscheduler"]["level"] = "DEBUG"

    # Prepend log_dir path to the file handler file name
    file_handler_filename = logging_config["handlers"]["file_handler"]["filename"]
    logging_config["handlers"]["file_handler"]["filename"] = str(
        log_dir / file_handler_filename)

    if config_class.LOG_TO_STDOUT:
        handlers.append("stream_handler")
        if config_class.DEVELOPMENT:
            logging_config["loggers"]["virtual"]["handlers"] = ["stream_handler"]

    if config_class.LOG_TO_FILE:
        handlers.append("file_handler")

    logging.config.dictConfig(logging_config)
