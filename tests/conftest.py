import tempfile
import shutil

import pytest

from src.config_parser import GeneralConfig, SpecificConfig
from src.ecosystem import Ecosystem
from src.engine import Engine
from src.subroutines import Climate, Light, Sensors
from src.utils import SingletonMeta
from config import Config


from .utils import ECOSYSTEM_UID, TESTING_ECOSYSTEM_CFG


Config.TESTING = True


@pytest.fixture(scope="session")
def temp_dir():
    temp_dir = tempfile.mkdtemp(prefix="gaia-")
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture(scope="session")
def general_config(temp_dir):
    config = GeneralConfig(temp_dir)
    config.ecosystems_config = TESTING_ECOSYSTEM_CFG
    yield config


@pytest.fixture
def specific_config(general_config):
    config = SpecificConfig(general_config, ECOSYSTEM_UID)
    yield config


@pytest.fixture(scope="session")  # Actually singleton
def engine(general_config):
    engine = Engine(general_config)
    yield engine


@pytest.fixture
def ecosystem(engine):
    ecosystem = Ecosystem(ECOSYSTEM_UID, engine)
    yield ecosystem


@pytest.fixture
def climate_subroutine(ecosystem):
    climate_subroutine = Climate(ecosystem)
    yield climate_subroutine


@pytest.fixture
def light_subroutine(ecosystem):
    light_subroutine = Light(ecosystem)
    yield light_subroutine


@pytest.fixture
def sensors_subroutine(ecosystem):
    sensor_subroutine = Sensors(ecosystem)
    yield sensor_subroutine


@pytest.fixture
def subroutines_list(climate_subroutine, light_subroutine, sensors_subroutine):
    return [climate_subroutine, light_subroutine, sensors_subroutine]
