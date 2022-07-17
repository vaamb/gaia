import tempfile
import shutil

import pytest

from src.config_parser import GeneralConfig
from src.ecosystem import Ecosystem
from src.engine import Engine
from src.subroutines import Light, Sensors

from .utils import ECOSYSTEM_UID, TESTING_ECOSYSTEM_CFG


@pytest.fixture(scope="session")
def temp_dir():
    temp_dir = tempfile.mkdtemp(prefix="gaiaEngine-")
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def general_config(temp_dir):
    config = GeneralConfig(base_dir=temp_dir)
    config.ecosystems_config = TESTING_ECOSYSTEM_CFG
    yield config


@pytest.fixture
def engine(general_config):
    engine = Engine(general_config)
    yield engine


@pytest.fixture
def ecosystem(engine):
    ecosystem = Ecosystem(ECOSYSTEM_UID, engine)
    yield ecosystem


@pytest.fixture
def light_subroutine(ecosystem):
    light_subroutine = Light(ecosystem)
    yield light_subroutine


@pytest.fixture
def sensors_subroutine(ecosystem):
    sensor_subroutine = Sensors(ecosystem)
    yield sensor_subroutine
