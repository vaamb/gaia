import os
import uuid


class BaseConfig:
    DEBUG = False
    TESTING = False
    DEVELOPMENT = False

    DIR = os.environ.get("GAIA_DIR") or os.getcwd()

    @property
    def LOG_DIR(self):
        return os.environ.get("GAIA_LOG_DIR") or os.path.join(self.DIR, "logs")

    @property
    def CACHE_DIR(self):
        return os.environ.get("GAIA_CACHE_DIR") or os.path.join(self.DIR, ".cache")

    LOG_TO_STDOUT = True
    LOG_TO_FILE = True
    LOG_ERROR = True

    ENGINE_UID = os.environ.get("GAIA_UID") or hex(uuid.getnode())[2:]
    VIRTUALIZATION = os.environ.get("GAIA_VIRTUALIZATION", False)
    VIRTUALIZATION_PARAMETERS = {"world": {},"ecosystems": {}}

    USE_DATABASE = False

    @property
    def SQLALCHEMY_DATABASE_URI(self):
        return (
            os.environ.get("GAIA_DATABASE_URI") or
            "sqlite+aiosqlite:///" + os.path.join(self.DIR, "gaia_data.db")
        )

    COMMUNICATE_WITH_OURANOS = False
    AGGREGATOR_COMMUNICATION_URL = (
        os.environ.get("GAIA_COMMUNICATION_URL") or
        "amqp://"
    )
    OURANOS_SECRET_KEY = os.environ.get("OURANOS_SECRET_KEY") or "secret_key"

    HEALTH_LOGGING_TIME = "00h00"
    CONFIG_WATCHER_PERIOD = 500  # in ms
    CLIMATE_LOOP_PERIOD = 10.0  # in s, rem: should be a multiple of SENSORS_LOOP_PERIOD
    LIGHT_LOOP_PERIOD = 0.5  # in s
    PICTURE_TAKING_PERIOD = 20.0  # in seconds
    PICTURE_SENDING_PERIOD = 120.0  # in seconds, should be a multiple of previous
    PICTURE_SIZE = (1640, 1232)  # in pixel
    SENSORS_LOOP_PERIOD = 10.0  # in s
    SENSORS_LOGGING_PERIOD = "*/10"  # in minute, cron-style
