import asyncio
from copy import deepcopy
import os
import shutil
import sys
import tempfile
from typing import Generator, TypeVar

import pytest
import pytest_asyncio

import gaia_validators as gv

from gaia.actuator_handler import ActuatorHandler
from gaia.config import BaseConfig, EcosystemConfig, EngineConfig, GaiaConfigHelper
from gaia.config.from_files import _MetaEcosystemConfig, PrivateConfigValidator
from gaia.ecosystem import Ecosystem
from gaia.engine import Engine
from gaia.subroutines import (
    Climate, Light, Sensors, subroutine_dict, subroutine_names)
from gaia.utils import SingletonMeta, yaml

from .data import (
    ecosystem_info, ecosystem_name, engine_uid, light_info, light_uid,
    place_latitude, place_longitude, place_name)
from .subroutines.dummy_subroutine import Dummy
from .utils import get_logs_content


T = TypeVar("T")

YieldFixture = Generator[T, None, None]


@pytest.fixture(scope="session")
def event_loop():
    if sys.platform.startswith("win") and sys.version_info[:2] >= (3, 8):
        # Avoid "RuntimeError: Event loop is closed" on Windows when tearing down tests
        # https://github.com/encode/httpx/issues/914
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def patch() -> None:
    # Patch subroutine dict and list to add the dummy subroutine
    subroutine_dict["dummy"] = Dummy
    subroutine_names.append("dummy")

    # Patch gaia_validators.ManagementFlags to add the dummy subroutine
    from enum import IntFlag
    management_flags = {
        flag.name: flag.value
        for flag in gv.ManagementFlags.__members__.values()
    }
    max_flag = max(*[flag.value for flag in gv.ManagementFlags])
    management_flags["dummy"] = management_flags["dummy_enabled"] = max_flag * 2
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
        ENGINE_UID = engine_uid

    GaiaConfigHelper.set_config(Config)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def engine_config_master(testing_cfg: None) -> YieldFixture[EngineConfig]:
    engine_config = EngineConfig()
    await engine_config.initialize_configs()

    engine_config._private_config = PrivateConfigValidator(**{
        "places": {
            place_name: gv.Coordinates(
                latitude=place_latitude,
                longitude=place_longitude,
            ),
        },
    }).model_dump()

    yield engine_config


@pytest_asyncio.fixture(scope="function", autouse=True)
async def engine_config(engine_config_master: EngineConfig) -> YieldFixture[EngineConfig]:
    app_config = deepcopy(engine_config_master.app_config)
    ecosystem_config = deepcopy(engine_config_master.ecosystems_config_dict)
    private_config = deepcopy(engine_config_master.private_config)
    for files in engine_config_master.cache_dir.iterdir():
        files.unlink()
    with get_logs_content(engine_config_master.logs_dir / "gaia.log"):
        pass  # Clear logs

    try:
        yield engine_config_master
    finally:
        engine_config_master.app_config = app_config
        engine_config_master.ecosystems_config_dict = ecosystem_config
        engine_config_master.private_config = private_config
        engine_config_master.chaos_memory = {}
        engine_config_master.sun_times = {}
        if engine_config_master.started:
            engine_config_master.stop_watchdog()


@pytest_asyncio.fixture(scope="function", autouse=True)
async def engine(engine_config: EngineConfig) -> YieldFixture[Engine]:
    engine = Engine(engine_config=engine_config)
    with get_logs_content(engine_config.logs_dir / "gaia.log"):
        pass  # Clear logs

    try:
        yield engine
    finally:
        if engine.started:
            engine.stop()
        SingletonMeta.detach_instance("Engine")
        del engine


@pytest_asyncio.fixture(scope="function")
async def ecosystem_config(engine_config: EngineConfig) -> YieldFixture[EcosystemConfig]:
    ecosystem_config = engine_config.get_ecosystem_config(ecosystem_name)
    with get_logs_content(ecosystem_config.general.logs_dir / "gaia.log"):
        pass  # Clear logs

    try:
        yield ecosystem_config
    finally:
        del _MetaEcosystemConfig.instances[ecosystem_config.uid]
        del ecosystem_config


@pytest_asyncio.fixture(scope="function")
async def ecosystem(engine: Engine) -> YieldFixture[Ecosystem]:
    ecosystem = engine.get_ecosystem(ecosystem_name)
    with get_logs_content(engine.config.logs_dir / "gaia.log"):
        pass  # Clear logs

    try:
        yield ecosystem
    finally:
        if ecosystem.started:
            await ecosystem.stop()
        del ecosystem


@pytest_asyncio.fixture(scope="function")
async def climate_subroutine(ecosystem: Ecosystem) -> YieldFixture[Climate]:
    climate_subroutine: Climate = ecosystem.subroutines["climate"]

    # Sensors subroutine is required ...
    await ecosystem.enable_subroutine("sensors")
    await ecosystem.start_subroutine("sensors")

    # ... as well as a climate parameter
    ecosystem.config.set_climate_parameter(
        "temperature",
        **{"day": 25, "night": 20, "hysteresis": 2}
    )

    try:
        yield climate_subroutine
    finally:
        if ecosystem.get_subroutine_status("sensors"):
            await ecosystem.stop_subroutine("sensors")
        if climate_subroutine.started:
            await climate_subroutine.stop()


@pytest_asyncio.fixture(scope="function")
async def light_subroutine(ecosystem: Ecosystem) -> YieldFixture[Light]:
    light_subroutine: Light = ecosystem.subroutines["light"]

    try:
        yield light_subroutine
    finally:
        if light_subroutine.started:
            await light_subroutine.stop()


@pytest_asyncio.fixture(scope="function")
async def sensors_subroutine(ecosystem: Ecosystem) -> YieldFixture[Sensors]:
    sensor_subroutine: Sensors = ecosystem.subroutines["sensors"]

    try:
        yield sensor_subroutine
    finally:
        if sensor_subroutine.started:
            await sensor_subroutine.stop()


@pytest_asyncio.fixture(scope="function")
async def dummy_subroutine(ecosystem: Ecosystem) -> YieldFixture[Sensors]:
    dummy_subroutine: Sensors = ecosystem.subroutines["dummy"]

    try:
        yield dummy_subroutine
    finally:
        if dummy_subroutine.started:
            await dummy_subroutine.stop()


@pytest_asyncio.fixture(scope="function")
async def light_handler(ecosystem: Ecosystem) -> YieldFixture[ActuatorHandler]:
    hardware_config = gv.HardwareConfig(uid=light_uid, **light_info)
    light_subroutine = ecosystem.subroutines["light"]
    await light_subroutine.add_hardware(hardware_config)

    light_handler: ActuatorHandler = ecosystem.get_actuator_handler("light")

    yield light_handler
