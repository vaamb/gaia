from copy import deepcopy
import os
import shutil
import tempfile
from time import monotonic
from typing import Generator, TypeVar

from pydantic import RootModel
import pytest
import pytest_asyncio

import gaia_validators as gv

from gaia.actuator_handler import ActuatorHandler
from gaia.config import BaseConfig, EcosystemConfig, EngineConfig, GaiaConfigHelper
from gaia.ecosystem import Ecosystem
from gaia.engine import Engine
from gaia.events import Events
from gaia.subroutines import (
    Climate, Health, Light, Pictures, Sensors, subroutine_dict, subroutine_names)
from gaia.utils import SingletonMeta, yaml

from .data import ecosystem_info, ecosystem_name, engine_uid
from .subroutines.dummy_subroutine import Dummy
from .utils import get_logs_content, MockDispatcher


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

    class RootEcosystemsConfigValidator(RootModel):
        root: dict[str, EcosystemConfigValidator]

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
        TESTING = True
        LOG_TO_STDOUT = False
        VIRTUALIZATION = True
        DIR = temp_dir
        ENGINE_UID = engine_uid
        AGGREGATOR_COMMUNICATION_URL = "memory:///"
        CONFIG_WATCHER_PERIOD = 100

        @property
        def SQLALCHEMY_DATABASE_URI(self):
            return "sqlite+aiosqlite://"

        # Make sure routines are only called on purpose
        SENSORS_LOOP_PERIOD = 25.0
        CLIMATE_LOOP_PERIOD = 25.0
        LIGHT_LOOP_PERIOD = 25.0
        PICTURE_TAKING_PERIOD = 25.0
        PICTURE_SENDING_PERIOD = 25.0
        PICTURE_SIZE = (42, 21)

    GaiaConfigHelper.set_config(Config)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def engine_config_master(testing_cfg: None) -> YieldFixture[EngineConfig]:
    engine_config = EngineConfig()
    await engine_config.initialize_configs()

    yield engine_config


@pytest_asyncio.fixture(scope="function", autouse=True)
async def engine_config(engine_config_master: EngineConfig) -> YieldFixture[EngineConfig]:
    app_config = deepcopy(engine_config_master.app_config)
    ecosystem_config = deepcopy(engine_config_master.ecosystems_config_dict)
    private_config = deepcopy(engine_config_master.private_config)
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
        if engine_config_master.cache_dir.iterdir():
            shutil.rmtree(engine_config_master.cache_dir)
            engine_config_master._dirs.pop("CACHE_DIR")


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
        del ecosystem_config


@pytest_asyncio.fixture(scope="function")
async def ecosystem(engine: Engine) -> YieldFixture[Ecosystem]:
    ecosystem = engine.get_ecosystem(ecosystem_name)
    ecosystem.virtual_self.start()
    with get_logs_content(engine.config.logs_dir / "gaia.log"):
        pass  # Clear logs

    await ecosystem.refresh_hardware()

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
        **{"day": 42.0, "night": 42.0, "hysteresis": 1.0, "alarm": 0.5}
    )

    try:
        yield climate_subroutine
    finally:
        if ecosystem.get_subroutine_status("sensors"):
            await ecosystem.stop_subroutine("sensors")
        if climate_subroutine.started:
            await climate_subroutine.stop()


@pytest_asyncio.fixture(scope="function")
async def health_subroutine(ecosystem: Ecosystem) -> YieldFixture[Health]:
    ecosystem.config.set_management("camera", True)
    health_subroutine: Health = ecosystem.subroutines["health"]

    try:
        yield health_subroutine
    finally:
        if health_subroutine.started:
            await health_subroutine.stop()


@pytest_asyncio.fixture(scope="function")
async def light_subroutine(ecosystem: Ecosystem) -> YieldFixture[Light]:
    light_subroutine: Light = ecosystem.subroutines["light"]

    try:
        yield light_subroutine
    finally:
        if light_subroutine.started:
            await light_subroutine.stop()


@pytest_asyncio.fixture(scope="function")
async def pictures_subroutine(ecosystem: Ecosystem) -> YieldFixture[Pictures]:
    ecosystem.config.set_management("camera", True)
    pictures_subroutine: Pictures = ecosystem.subroutines["pictures"]

    try:
        yield pictures_subroutine
    finally:
        if pictures_subroutine.started:
            await pictures_subroutine.stop()


@pytest_asyncio.fixture(scope="function")
async def sensors_subroutine(ecosystem: Ecosystem) -> YieldFixture[Sensors]:
    sensor_subroutine: Sensors = ecosystem.subroutines["sensors"]

    try:
        yield sensor_subroutine
    finally:
        if sensor_subroutine.started:
            await sensor_subroutine.stop()


@pytest_asyncio.fixture(scope="function")
async def dummy_subroutine(ecosystem: Ecosystem) -> YieldFixture[Dummy]:
    dummy_subroutine: Sensors = ecosystem.subroutines["dummy"]

    try:
        yield dummy_subroutine
    finally:
        if dummy_subroutine.started:
            await dummy_subroutine.stop()


@pytest_asyncio.fixture(scope="function")
async def light_handler(ecosystem: Ecosystem) -> YieldFixture[ActuatorHandler]:
    light_handler: ActuatorHandler = ecosystem.get_actuator_handler("light")
    light_handler.activate()

    yield light_handler


@pytest.fixture(scope="module")
def mock_dispatcher_module()  -> MockDispatcher:
    mock_dispatcher = MockDispatcher("gaia")
    return mock_dispatcher


@pytest_asyncio.fixture(scope="function")
async def mock_dispatcher(
        mock_dispatcher_module: MockDispatcher,
        engine: Engine,
) -> YieldFixture[MockDispatcher]:
    engine.config.app_config.COMMUNICATE_WITH_OURANOS = True
    engine.message_broker = mock_dispatcher_module
    await engine.start_message_broker()

    try:
        yield mock_dispatcher_module
    finally:
        await engine.stop_message_broker()
        mock_dispatcher_module.clear_store()
        engine.config.app_config.COMMUNICATE_WITH_OURANOS = False


@pytest_asyncio.fixture(scope="function")
async def events_handler(
        ecosystem: Ecosystem,
        mock_dispatcher: MockDispatcher,
) -> YieldFixture[Events]:
    events_handler = Events(ecosystem.engine)
    mock_dispatcher.register_event_handler(events_handler)
    ecosystem.engine.event_handler = events_handler

    try:
        yield events_handler
    finally:
        mock_dispatcher.clear_store()


@pytest_asyncio.fixture(scope="function")
async def registered_events_handler(
        events_handler: Events,
) -> YieldFixture[Events]:
    events_handler._last_heartbeat = monotonic()
    assert events_handler.is_connected()
    events_handler.registered = True

    try:
        yield events_handler
    finally:
        events_handler.registered = False
