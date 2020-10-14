# -*- coding: utf-8 -*-
import logging
from logging import config
import os
import uuid

class Config(): 
    DEBUG = False
    LOG_TO_STDOUT = True

    TEST_CONNECTION_IP = "1.1.1.1"
    GAIAWEB = ("192.168.1.111", 5000)
    
    UID = hex(uuid.getnode())[2:]
    
    HEALTH_LOGGING_TIME = "00h00"
    CONFIG_WATCHER_PERIOD = 2
    LIGHT_LOOP_PERIOD = 0.5
    SENSORS_TIMEOUT = 30

def configure_logging():
    DEBUG = Config.DEBUG
    LOG_TO_STDOUT = Config.LOG_TO_STDOUT

    handler = "streamHandler"
    if not LOG_TO_STDOUT:
        if not os.path.exists("logs"):
            os.mkdir("logs")
        handler = "fileHandler"

    LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,

    "formatters": {
        "streamFormat":{
            "format": "%(asctime)s [%(levelname)-4.4s] Thread:%(thread)-5.5d %(name)-20.20s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S"
            },
        "fileFormat": {
            "format": "%(asctime)s -- %(levelname)s  -- %(name)s -- %(message)s",
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
            "filename": "logs/gaia.log",
            "mode": "w",
            "maxBytes": 1024*32,
            "backupCount": 5,
            },
        },

    "loggers": {
        "": {
            "handlers": [handler],
            "level": f"{'DEBUG' if DEBUG else 'INFO'}"
            },
        "apscheduler": {
            "handlers": [handler],
            "level": "WARNING"
            },
        "urllib3": {
            "handlers": [handler],
            "level": "WARNING"
            },
        },
    }

    logging.config.dictConfig(LOGGING_CONFIG)

configure_logging()