import os

from gaia.config import BaseConfig


class Config(BaseConfig):
    DEBUG = False
    TESTING = True

    VIRTUALIZATION = True

    USE_DATABASE = True

    COMMUNICATE_WITH_OURANOS = True
    AGGREGATOR_COMMUNICATION_URL = "amqp://"
    OURANOS_SECRET_KEY = os.environ.get("OURANOS_SECRET_KEY") or "secret_key"
