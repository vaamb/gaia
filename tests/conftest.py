from enum import IntFlag
import os
import shutil
import tempfile
from typing import Generator, Type, TypeVar

import pytest

import gaia_validators as gv

from gaia.config import EcosystemConfig, EngineConfig, GaiaConfig, set_config
from gaia.ecosystem import Ecosystem
from gaia.engine import Engine
from gaia.subroutines import (
    Climate, Light, Sensors, subroutine_dict, subroutine_names)
from gaia.utils import yaml

from .data import ecosystem_info, ecosystem_name
from .dummy_subroutine import Dummy
from .utils import get_logs_content


T = TypeVar("T")

YieldFixture = Generator[T, None, None]


@pytest.fixture(scope="session")
def patch() -> None:
    # Patch subroutine dict and list to add the dummy subroutine
    subroutine_dict["dummy"] = Dummy
    subroutine_names.append("dummy")

    # Patch gaia_validators.ManagementFlags to add the dummy subroutine
    from enum import IntFlag
    management_flags = {
        flag.name: flag.value
        for flag in gv.ManagementFlags
    }
    management_flags["dummy"] = max(management_flags.values()) * 2
    gv.ManagementFlags = IntFlag("ManagementFlags", management_flags)

    # Patch gaia_validators.ManagementConfig to add the dummy subroutine
    class ManagementConfig(gv.ManagementConfig):
        dummy: bool = False

    gv.ManagementConfig = ManagementConfig

    # Patch gaia.config.from_files to use the new ManagementConfig
    from pydantic import Field
    from gaia.config import from_files

    class EcosystemConfigValidator(from_files.EcosystemConfigValidator):
        management: gv.ManagementConfig = Field(default_factory=gv.ManagementConfig)

    class RootEcosystemsConfigValidator(gv.BaseModel):
        config: dict[str, EcosystemConfigValidator]

    from_files.RootEcosystemsConfigValidator = RootEcosystemsConfigValidator

    yield


@pytest.fixture(scope="session")
def testing_cfg(patch) -> YieldFixture[GaiaConfig]:
    temp_dir = tempfile.mkdtemp(prefix="gaia-")
    GaiaConfig.LOG_TO_STDOUT = False
    GaiaConfig.TESTING = True
    GaiaConfig.DIR = temp_dir
    set_config(GaiaConfig)
    with open(os.path.join(temp_dir, "ecosystems.cfg"), "w") as file:
        yaml.dump(ecosystem_info, file)
    yield GaiaConfig
    shutil.rmtree(temp_dir)


@pytest.fixture(scope="function")
def engine_config(testing_cfg: Type[GaiaConfig]) -> YieldFixture[EngineConfig]:
    engine_config = EngineConfig(gaia_config=testing_cfg())
    engine_config.initialize_configs()
    for files in engine_config.cache_dir.iterdir():
        files.unlink()
    with get_logs_content(engine_config.logs_dir/"base.log"):
        pass  # Clear logs
    yield engine_config
    del engine_config


@pytest.fixture(scope="function")
def engine(engine_config: EngineConfig) -> YieldFixture[Engine]:
    engine = Engine(engine_config=engine_config)
    with get_logs_content(engine_config.logs_dir/"base.log"):
        pass  # Clear logs
    yield engine
    del engine


@pytest.fixture(scope="function")
def ecosystem_config(engine_config: EngineConfig) -> YieldFixture[EcosystemConfig]:
    ecosystem_config = engine_config.get_ecosystem_config(ecosystem_name)
    with get_logs_content(ecosystem_config.general.logs_dir/"base.log"):
        pass  # Clear logs
    yield ecosystem_config
    del ecosystem_config


@pytest.fixture(scope="function")
def ecosystem(engine: Engine) -> YieldFixture[Ecosystem]:
    ecosystem = engine.get_ecosystem(ecosystem_name)
    with get_logs_content(engine.config.logs_dir/"base.log"):
        pass  # Clear logs
    yield ecosystem
    del ecosystem


@pytest.fixture(scope="function")
def climate_subroutine(ecosystem: Ecosystem) -> YieldFixture[Climate]:
    climate_subroutine: Climate = ecosystem.subroutines["climate"]
    yield climate_subroutine


@pytest.fixture(scope="function")
def light_subroutine(ecosystem: Ecosystem) -> YieldFixture[Light]:
    light_subroutine: Light = ecosystem.subroutines["light"]
    yield light_subroutine


@pytest.fixture(scope="function")
def sensors_subroutine(ecosystem: Ecosystem) -> YieldFixture[Sensors]:
    sensor_subroutine: Sensors = ecosystem.subroutines["sensors"]
    yield sensor_subroutine


@pytest.fixture(scope="function")
def dummy_subroutine(ecosystem: Ecosystem) -> YieldFixture[Sensors]:
    sensor_subroutine: Sensors = ecosystem.subroutines["dummy"]
    yield sensor_subroutine
