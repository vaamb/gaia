import os
import uuid


class Config:
    APP_NAME = "Gaia"
    VERSION = "0.5.2"

    DEBUG = False
    TESTING = True
    VIRTUALIZATION = True

    USE_DATABASE = True
    USE_BROKER = True

    LOG_TO_STDOUT = True
    LOG_TO_FILE = True
    LOG_ERROR = True

    UUID = os.environ.get("GAIA_UUID") or hex(uuid.getnode())[2:]

    TEST_CONNECTION_IP = "1.1.1.1"
    MESSAGE_BROKER_URL = "amqp://"  # "socketio://127.0.0.1:5000" if any((DEBUG, TESTING)) else "socketio://192.168.1.111:5000"
    OURANOS_SECRET_KEY = os.environ.get("OURANOS_SECRET_KEY") or \
        "BXhNmCEmNdoBNngyGXj6jJtooYAcKpt6"
    HEALTH_LOGGING_TIME = "00h00"
    CONFIG_WATCHER_PERIOD = 10
    LIGHT_LOOP_PERIOD = 0.5
    SENSORS_TIMEOUT = 30
