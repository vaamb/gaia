import os
import uuid


class Config:
    APP_NAME = "Gaia"
    VERSION = "0.5.2"

    DEBUG = False
    TESTING = True

    LOG_TO_STDOUT = True
    LOG_TO_FILE = True
    LOG_ERROR = True
    USE_DATABASE = True  # TODO: check for url, if found use it
    USE_BROKER = True  # TODO: check for url, if found use it

    # BASE_DIR = ~/Gaia
    UUID = os.environ.get("GAIA_UUID") or hex(uuid.getnode())[2:]
    VIRTUALIZATION = os.environ.get("GAIA_VIRTUALIZATION", False)
    MESSAGE_BROKER_URL = os.environ.get("OURANOS_AGGREGATOR_URL") or "amqp://"  # "socketio://127.0.0.1:5000"
    DATABASE_URI = os.environ.get("GAIA_DATABASE_URI", False)
    # If not provided, will be f"sqlite:///{base_dir/'gaia_data.db'}"
    OURANOS_SECRET_KEY = os.environ.get("OURANOS_SECRET_KEY") or \
        "BXhNmCEmNdoBNngyGXj6jJtooYAcKpt6"

    HEALTH_LOGGING_TIME = "00h00"
    CONFIG_WATCHER_PERIOD = 10
    LIGHT_LOOP_PERIOD = 0.5
    SENSORS_TIMEOUT = 30

    TEST_CONNECTION_IP = "1.1.1.1"
