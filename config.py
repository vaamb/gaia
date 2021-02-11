import logging
from logging import config
import os
from pathlib import Path
import uuid


base_dir = Path(__file__).absolute().parents[0]


class Config:
    DEBUG = False
    TESTING = True  # When true, changes won't be save

    LOG_TO_STDOUT = True
    LOG_TO_FILE = False
    LOG_ERROR = True

    TEST_CONNECTION_IP = "1.1.1.1"
    GAIAWEB = ("127.0.0.1", 5000)
    # GAIAWEB = ("192.168.1.111", 5000)
    UID = hex(uuid.getnode())[2:]

    HEALTH_LOGGING_TIME = "00h00"
    CONFIG_WATCHER_PERIOD = 2
    LIGHT_LOOP_PERIOD = 0.5
    SENSORS_TIMEOUT = 30


def configure_logging(config_class):
    DEBUG = config_class.DEBUG
    LOG_TO_STDOUT = config_class.LOG_TO_STDOUT
    LOG_TO_FILE = config_class.LOG_TO_FILE
    LOG_ERROR = config_class.LOG_ERROR

    handlers = []

    if LOG_TO_STDOUT:
        handlers.append("streamHandler")

    if LOG_TO_FILE or LOG_ERROR:
        if not os.path.exists(base_dir/"logs"):
            os.mkdir(base_dir/"logs")

    if LOG_TO_FILE:
        handlers.append("fileHandler")

    if LOG_ERROR:
        handlers.append("errorFileHandler")

    LOGGING_CONFIG = {
        "version": 1,
        "disable_existing_loggers": False,

        "formatters": {
            "streamFormat": {
                "format": "%(asctime)s [%(levelname)-4.4s] %(name)-20.20s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S"
            },
            "fileFormat": {
                "format": "%(asctime)s -- %(levelname)s  -- %(name)s -- %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S"
            },
            "errorFormat": {
                'format': '%(asctime)s %(levelname)-4.4s %(module)-17s ' +
                          'line:%(lineno)-4d  %(message)s',
                "datefmt": "%Y-%m-%d %H:%M:%S"
            },
        },

        "handlers": {
            "streamHandler": {
                "level": f"{'DEBUG' if DEBUG else 'INFO'}",
                "formatter": "streamFormat",
                "class": "logging.StreamHandler",
            },
        },

        "loggers": {
            "": {
                "handlers": handlers,
                "level": f"{'DEBUG' if DEBUG else 'INFO'}"
            },
            "apscheduler": {
                "handlers": handlers,
                "level": "WARNING"
            },
            "urllib3": {
                "handlers": handlers,
                "level": "WARNING"
            },
            "engineio": {
                "handlers": handlers,
                "level": "WARNING"
            },
            "socketio": {
                "handlers": handlers,
                "level": "WARNING"
            },
        },
    }

    # Append file handlers to config as if they are needed they require logs file
    if LOG_TO_FILE:
        LOGGING_CONFIG["handlers"].update({
            "fileHandler": {
                "level": f"{'DEBUG' if DEBUG else 'INFO'}",
                "formatter": "fileFormat",
                "class": "logging.handlers.RotatingFileHandler",
                'filename': 'logs/gaiaEngine.log',
                'mode': 'w+',
                'maxBytes': 1024 * 512,
                'backupCount': 5,
            }
        })

    if LOG_ERROR:
        LOGGING_CONFIG["handlers"].update({
            "errorFileHandler": {
                "level": f"ERROR",
                "formatter": "errorFormat",
                "class": "logging.FileHandler",
                'filename': 'logs/gaiaEngine_errors.log',
                'mode': 'a',
            }
        })

    logging.config.dictConfig(LOGGING_CONFIG)

configure_logging(Config)
