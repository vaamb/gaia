from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
import shutil
import tempfile
from typing import Generator, Type, TypeVar

import pytest

import gaia_validators as gv

from gaia.actuator_handler import ActuatorHandler
from gaia.config import BaseConfig, EcosystemConfig, EngineConfig, GaiaConfigHelper
from gaia.ecosystem import Ecosystem
from gaia.engine import Engine
from gaia.subroutines import (
    Climate, Light, Sensors, subroutine_dict, subroutine_names)
from gaia.utils import SingletonMeta, yaml

from .data import ecosystem_info, ecosystem_name, light_info, light_uid
from .subroutines.dummy_subroutine import Dummy
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
def temp_dir(patch) -> YieldFixture[str]:
    temp_dir = tempfile.mkdtemp(prefix="gaia-")
    with open(os.path.join(temp_dir, "ecosystems.cfg"), "w") as file:
        yaml.dump(ecosystem_info, file)

    yield temp_dir

    shutil.rmtree(temp_dir)


@pytest.fixture(scope="session")
def testing_cfg(temp_dir) -> None:
    class Config(BaseConfig):
        LOG_TO_STDOUT = False
        TESTING = True
        VIRTUALIZATION = True
        DIR = temp_dir
        AGGREGATOR_COMMUNICATION_URL = "memory:///"
        CONFIG_WATCHER_PERIOD = 100

    GaiaConfigHelper.set_config(Config)


@pytest.fixture(scope="session", autouse=True)
def engine_config_master(testing_cfg: None) -> YieldFixture[EngineConfig]:
    engine_config = EngineConfig()
    engine_config.initialize_configs()

    yield engine_config

    SingletonMeta.clear_instances()


@pytest.fixture(scope="function", autouse=True)
def engine_config(engine_config_master: EngineConfig) -> YieldFixture[EngineConfig]:
    app_config = deepcopy(engine_config_master.app_config)
    for files in engine_config_master.cache_dir.iterdir():
        files.unlink()
    with get_logs_content(engine_config_master.logs_dir / "gaia.log"):
        pass  # Clear logs

    try:
        yield engine_config_master
    finally:
        engine_config_master.app_config = app_config
        engine_config_master.ecosystems_config_dict = deepcopy(ecosystem_info)
        engine_config_master.private_config = {}
        engine_config_master.chaos_memory = {}
        engine_config_master.sun_times = {}
        engine_config_master._executor = ThreadPoolExecutor(
                thread_name_prefix=f"Engine_ThreadPoolExecutor", max_workers=10)

        if engine_config_master.started:
            raise


@pytest.fixture(scope="function", autouse=True)
def engine(engine_config: EngineConfig) -> YieldFixture[Engine]:
    engine = Engine(engine_config=engine_config)
    with get_logs_content(engine_config.logs_dir / "gaia.log"):
        pass  # Clear logs

    yield engine

    if engine.started:
        engine.stop()
    del engine


@pytest.fixture(scope="function")
def ecosystem_config(engine_config: EngineConfig) -> YieldFixture[EcosystemConfig]:
    ecosystem_config = engine_config.get_ecosystem_config(ecosystem_name)
    with get_logs_content(ecosystem_config.general.logs_dir / "gaia.log"):
        pass  # Clear logs

    yield ecosystem_config

    del ecosystem_config


@pytest.fixture(scope="function")
def ecosystem(engine: Engine) -> YieldFixture[Ecosystem]:
    ecosystem = engine.get_ecosystem(ecosystem_name)
    with get_logs_content(engine.config.logs_dir / "gaia.log"):
        pass  # Clear logs

    yield ecosystem

    if ecosystem.started:
        ecosystem.stop()
    del ecosystem


@pytest.fixture(scope="function")
def climate_subroutine(ecosystem: Ecosystem) -> YieldFixture[Climate]:
    climate_subroutine: Climate = ecosystem.subroutines["climate"]

    # Sensors subroutine is required ...
    ecosystem.enable_subroutine("sensors")
    ecosystem.start_subroutine("sensors")

    # ... as well as a climate parameter
    ecosystem.config.set_climate_parameter(
        "temperature",
        {"day": 25, "night": 20, "hysteresis": 2}
    )

    yield climate_subroutine

    if ecosystem.get_subroutine_status("sensors"):
        ecosystem.stop_subroutine("sensors")
    if climate_subroutine.started:
        climate_subroutine.stop()


@pytest.fixture(scope="function")
def light_subroutine(ecosystem: Ecosystem) -> YieldFixture[Light]:
    light_subroutine: Light = ecosystem.subroutines["light"]

    yield light_subroutine

    if light_subroutine.started:
        light_subroutine.stop()


@pytest.fixture(scope="function")
def sensors_subroutine(ecosystem: Ecosystem) -> YieldFixture[Sensors]:
    sensor_subroutine: Sensors = ecosystem.subroutines["sensors"]

    yield sensor_subroutine

    if sensor_subroutine.started:
        sensor_subroutine.stop()


@pytest.fixture(scope="function")
def dummy_subroutine(ecosystem: Ecosystem) -> YieldFixture[Sensors]:
    dummy_subroutine: Sensors = ecosystem.subroutines["dummy"]

    yield dummy_subroutine

    if dummy_subroutine.started:
        dummy_subroutine.stop()


@pytest.fixture(scope="function")
def light_handler(ecosystem: Ecosystem) -> YieldFixture[ActuatorHandler]:
    hardware_config = gv.HardwareConfig(uid=light_uid, **light_info)
    light_subroutine = ecosystem.subroutines["light"]
    light_subroutine.add_hardware(hardware_config)

    light_handler: ActuatorHandler = ecosystem.get_actuator_handler("light")

    yield light_handler
