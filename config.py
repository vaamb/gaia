import os
import uuid


class Config:
    DEBUG = False
    TESTING = True  # When true, changes won't be save
    VIRTUALIZATION = True

    LOG_TO_STDOUT = True
    LOG_TO_FILE = False
    LOG_ERROR = True

    TEST_CONNECTION_IP = "1.1.1.1"
    GAIAWEB = ("127.0.0.1", 5000)
    # GAIAWEB = ("192.168.1.111", 5000)
    UID = hex(uuid.getnode())[2:]

    GAIA_SECRET_KEY = os.environ.get("GAIA_SECRET_KEY") or \
                 "BXhNmCEmNdoBNngyGXj6jJtooYAcKpt6"

    HEALTH_LOGGING_TIME = "00h00"
    CONFIG_WATCHER_PERIOD = 10
    LIGHT_LOOP_PERIOD = 0.5
    SENSORS_TIMEOUT = 30
