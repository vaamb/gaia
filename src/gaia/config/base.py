import os
import uuid


DIR = os.environ.get("GAIA_DIR") or os.getcwd()


class BaseConfig:
    DEBUG = False
    TESTING = False

    LOG_DIR = os.environ.get("GAIA_LOG_DIR") or os.path.join(DIR, ".logs")
    CACHE_DIR = os.environ.get("GAIA_CACHE_DIR") or os.path.join(DIR, ".cache")

    LOG_TO_STDOUT = True
    LOG_TO_FILE = True
    LOG_ERROR = True

    USE_DATABASE = False  # TODO: check for url, if found use it
    USE_BROKER = False  # TODO: check for url, if found use it

    # BASE_DIR = ~/Gaia
    UUID = os.environ.get("GAIA_UUID") or hex(uuid.getnode())[2:]
    VIRTUALIZATION = os.environ.get("GAIA_VIRTUALIZATION", False)
    AGGREGATOR_COMMUNICATION_URL = os.environ.get(
        "GAIA_COMMUNICATION_URL") or "amqp://"
    DATABASE_URI = os.environ.get("GAIA_DATABASE_URI", False)
    # If not provided, will be f"sqlite:///{base_dir/'gaia_data.db'}"
    OURANOS_SECRET_KEY = os.environ.get("OURANOS_SECRET_KEY") or "secret_key"
    HEALTH_LOGGING_TIME = "00h00"
    CONFIG_WATCHER_PERIOD = 10
    LIGHT_LOOP_PERIOD = 0.5
    SENSORS_TIMEOUT = 30

    SENSORS_LOGGING_PERIOD = None

    TEST_CONNECTION_IP = "1.1.1.1"
