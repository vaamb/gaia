from asyncio import Condition, Event
from copy import deepcopy
import os
import shutil
import tempfile
from time import monotonic
from typing import AsyncGenerator, Generator, TypeVar

import pytest
import pytest_asyncio

import gaia_validators as gv

from gaia.actuator_handler import ActuatorHandler
from gaia.config import BaseConfig, EcosystemConfig, EngineConfig, GaiaConfigHelper
from gaia.ecosystem import Ecosystem
from gaia.engine import Engine
from gaia.events import Events
from gaia.hardware.abc import _MetaHardware
from gaia.utils import get_yaml, SingletonMeta
from gaia.virtual import VirtualWorld, VirtualEcosystem

from .data import ecosystem_info, ecosystem_uid, engine_uid
from .utils import MockDispatcher, yield_control


T = TypeVar("T")

YieldFixture = Generator[T, None, None]
AsyncYieldFixture = AsyncGenerator[T, None]


@pytest.fixture(scope="session")
def temp_dir() -> YieldFixture[str]:
    yaml = get_yaml()
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
async def engine_config_master(testing_cfg: None) -> AsyncYieldFixture[EngineConfig]:
    engine_config = EngineConfig()
    await engine_config.initialize_configs()

    yield engine_config


@pytest_asyncio.fixture(scope="function", autouse=True)
async def engine_config(
        engine_config_master: EngineConfig,
        caplog: pytest.LogCaptureFixture,
) -> AsyncYieldFixture[EngineConfig]:
    app_config = deepcopy(engine_config_master.app_config)
    ecosystem_config = deepcopy(engine_config_master.ecosystems_config_dict)
    private_config = deepcopy(engine_config_master.private_config)

    caplog.clear()

    try:
        yield engine_config_master
    finally:
        engine_config_master._app_config = app_config
        engine_config_master._ecosystems_config_dict = ecosystem_config
        engine_config_master._private_config = private_config
        engine_config_master._chaos_memory = {}
        engine_config_master._sun_times = {}
        if engine_config_master.started:
            engine_config_master.watchdog.stop()
        # Asyncio primitives bind to the first event loop that awaits them;
        # renew them so they can be awaited in the next test's event loop
        engine_config_master.watchdog.new_config = Condition()
        engine_config_master.watchdog._stop_event = Event()
        if engine_config_master.cache_dir.iterdir():
            shutil.rmtree(engine_config_master.cache_dir)
            engine_config_master.app_config._paths.pop("CACHE_DIR")


@pytest_asyncio.fixture(scope="function")
async def engine(
        engine_config: EngineConfig,
        caplog: pytest.LogCaptureFixture,
) -> AsyncYieldFixture[Engine]:
    engine = await Engine.initialize(engine_config=engine_config)

    caplog.clear()

    try:
        yield engine
    finally:
        if engine.started:
            await engine.stop()
        await engine.terminate()
        SingletonMeta.detach_instance("Engine")
        del engine
        # Since python 3.13, it sometimes takes more time to perform garbage
        # collection and reclaim the unreferenced hardware
        await yield_control()
        not_cleared = [_ for _ in _MetaHardware.instances]
        _MetaHardware.instances.clear()
        if not_cleared:
            raise RuntimeError(
                f"{' ,'.join(not_cleared)} still have open instance(s)"
            )


@pytest_asyncio.fixture(scope="function")
async def virtual_world(engine: Engine) -> AsyncYieldFixture[VirtualWorld]:
    yield engine.virtual_world


@pytest_asyncio.fixture(scope="function")
async def ecosystem_config(
        engine_config: EngineConfig,
        caplog: pytest.LogCaptureFixture,
) -> AsyncYieldFixture[EcosystemConfig]:
    ecosystem_config = engine_config.get_ecosystem_config(ecosystem_uid)

    caplog.clear()

    try:
        yield ecosystem_config
    finally:
        del ecosystem_config


@pytest_asyncio.fixture(scope="function")
async def ecosystem(
        engine: Engine,
        caplog: pytest.LogCaptureFixture,
) -> AsyncYieldFixture[Ecosystem]:
    await engine.initialize_ecosystems()
    ecosystem = engine.get_ecosystem(ecosystem_uid)
    ecosystem.virtual_self.start()
    await ecosystem.initialize_hardware()

    caplog.clear()

    try:
        yield ecosystem
    finally:
        if ecosystem.started:
            await ecosystem.stop()
        await ecosystem.terminate()
        del ecosystem


@pytest_asyncio.fixture(scope="function")
async def virtual_ecosystem(ecosystem: Ecosystem) -> AsyncYieldFixture[VirtualEcosystem]:
    ecosystem.virtual_self.time_between_measures = -1
    yield ecosystem.virtual_self


@pytest_asyncio.fixture(scope="function")
async def light_handler(ecosystem: Ecosystem) -> AsyncYieldFixture[ActuatorHandler]:
    # We need to hold the pid associated to light handler
    pid = ecosystem.actuator_hub.get_pid(gv.ClimateParameter.light)
    light_handler: ActuatorHandler = ecosystem.get_actuator_handler("light")
    light_handler.activate()

    yield light_handler

    light_handler.deactivate()

    del pid


@pytest.fixture(scope="module")
def mock_dispatcher_module()  -> MockDispatcher:
    mock_dispatcher = MockDispatcher("gaia")
    return mock_dispatcher


@pytest_asyncio.fixture(scope="function")
async def mock_dispatcher(
        mock_dispatcher_module: MockDispatcher,
        engine: Engine,
) -> AsyncYieldFixture[MockDispatcher]:
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
) -> AsyncYieldFixture[Events]:
    events_handler = Events(ecosystem.engine)
    mock_dispatcher.register_event_handler(events_handler)
    ecosystem.engine.event_handler = events_handler
    mock_dispatcher.clear_store()
    try:
        yield events_handler
    finally:
        mock_dispatcher.clear_store()


@pytest_asyncio.fixture(scope="function")
async def registered_events_handler(
        events_handler: Events,
) -> AsyncYieldFixture[Events]:
    events_handler._last_heartbeat = monotonic()
    assert events_handler.is_connected()
    events_handler.registered = True

    try:
        yield events_handler
    finally:
        events_handler.registered = False
