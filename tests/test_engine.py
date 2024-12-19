from asyncio import create_task, sleep, wait_for
from datetime import date

import pytest

from dispatcher import EventHandler, AsyncDispatcher
from sqlalchemy_wrapper import SQLAlchemyWrapper

from gaia import EcosystemConfig, Engine, EngineConfig

from .data import ecosystem_uid, sun_times
from .utils import get_logs_content


def test_engine_singleton(engine: Engine, engine_config: EngineConfig):
    assert engine is Engine(engine_config)


def test_engine_dict(engine: Engine, engine_config: EngineConfig):
    assert engine.config.__dict__ == engine_config.__dict__


def test_engine_plugins_needed(engine: Engine):
    assert not engine.plugins_needed

    # Test when only communication is required
    engine.config.app_config.COMMUNICATE_WITH_OURANOS = True
    assert engine.plugins_needed
    engine.config.app_config.COMMUNICATE_WITH_OURANOS = False

    # Test when only DB is required
    engine.config.app_config.USE_DATABASE = True
    assert engine.plugins_needed
    engine.config.app_config.USE_DATABASE = False

    # Test when both communication and DB are required
    engine.config.app_config.COMMUNICATE_WITH_OURANOS = True
    engine.config.app_config.USE_DATABASE = True
    assert engine.plugins_needed
    engine.config.app_config.COMMUNICATE_WITH_OURANOS = False
    engine.config.app_config.USE_DATABASE = False


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_engine_message_broker(engine: Engine):
    # Test when communication is disabled in config
    engine.config.app_config.COMMUNICATE_WITH_OURANOS = False
    assert engine.use_message_broker is False

    with pytest.raises(RuntimeError, match="COMMUNICATE_WITH_OURANOS"):
        await engine.init_message_broker()
    with pytest.raises(AttributeError):
        assert isinstance(engine.message_broker, AsyncDispatcher)
    with pytest.raises(AttributeError):
        assert isinstance(engine.event_handler, EventHandler)

    # Test when communication is enabled in config
    engine.config.app_config.COMMUNICATE_WITH_OURANOS = True
    assert engine.use_message_broker

    # Test invalid communication backend urls
    url = engine.config.app_config.AGGREGATOR_COMMUNICATION_URL

    engine.config.app_config.AGGREGATOR_COMMUNICATION_URL = None
    with pytest.raises(RuntimeError, match="AGGREGATOR_COMMUNICATION_URL"):
        await engine.init_message_broker()

    engine.config.app_config.AGGREGATOR_COMMUNICATION_URL = "Invalid"
    with pytest.raises(ValueError, match="is not a valid broker URL"):
        await engine.init_message_broker()

    engine.config.app_config.AGGREGATOR_COMMUNICATION_URL = "Invalid://"
    with pytest.raises(ValueError, match="is not supported"):
        await engine.init_message_broker()

    # Test message broker and event handler initialization
    engine.config.app_config.AGGREGATOR_COMMUNICATION_URL = url
    await engine.init_message_broker()
    with get_logs_content(engine.config.logs_dir / "gaia.log") as logs:
        assert "Initialising the event dispatcher" in logs

    assert engine._message_broker is not None
    assert isinstance(engine.message_broker, AsyncDispatcher)
    assert isinstance(engine.event_handler, EventHandler)

    # Test message broker start and stop
    await engine.start_message_broker()
    assert engine.message_broker_started
    await engine.stop_message_broker()
    assert not engine.message_broker_started


@pytest.mark.asyncio
async def test_engine_database(engine: Engine):
    # Test when DB is disabled in config
    engine.config.app_config.USE_DATABASE = False
    assert engine.use_db is False

    with pytest.raises(RuntimeError, match="USE_DATABASE"):
        await engine.init_database()
    assert engine.use_db is False
    with pytest.raises(AttributeError):
        assert isinstance(engine.db, SQLAlchemyWrapper)

    # Test DB initialization
    engine.config.app_config.USE_DATABASE = True
    assert engine.use_db is True

    await engine.init_database()
    with get_logs_content(engine.config.logs_dir / "gaia.log") as logs:
        assert "Initialising the database" in logs
    assert isinstance(engine.db, SQLAlchemyWrapper)

    # Test DB start and stop
    await engine.start_database()
    assert engine.db_started
    await engine.stop_database()
    assert not engine.db_started


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_engine_plugins(engine: Engine):
    assert engine.config.app_config.COMMUNICATE_WITH_OURANOS is False
    assert engine.config.app_config.USE_DATABASE is False
    assert engine.plugins_needed is False

    with pytest.raises(RuntimeError):
        await engine.init_plugins()
    with pytest.raises(RuntimeError):
        await engine.start_plugins()

    engine.config.app_config.COMMUNICATE_WITH_OURANOS = True
    engine.config.app_config.USE_DATABASE = True
    assert engine.plugins_needed is True

    await engine.init_plugins()
    with get_logs_content(engine.config.logs_dir / "gaia.log") as logs:
        assert "Initialising the plugins" in logs
    assert engine.plugins_initialized is True

    await engine.start_plugins()
    await engine.stop_plugins()

    # Reset the message broker and the database
    engine.message_broker = None
    engine.event_handler = None
    engine.db = None


@pytest.mark.asyncio
async def test_engine_background_tasks(engine: Engine):
    engine.start_background_tasks()
    with get_logs_content(engine.config.logs_dir / "gaia.log") as logs:
        assert "Starting the background tasks" in logs
    engine.stop_background_tasks()
    with get_logs_content(engine.config.logs_dir / "gaia.log") as logs:
        assert "Stopping the background tasks" in logs


@pytest.mark.asyncio
async def test_engine_states(engine: Engine):
    assert not engine.started
    assert not engine.running
    assert not engine.paused
    assert not engine.stopping
    assert not engine.stopped

    await engine.start()
    with get_logs_content(engine.config.logs_dir / "gaia.log") as logs:
        assert "Starting Gaia ..." in logs
    assert engine.started
    assert engine.running
    assert not engine.paused
    assert not engine.stopping
    assert not engine.stopped
    with pytest.raises(RuntimeError):
        await engine.resume()

    engine.pause()
    with get_logs_content(engine.config.logs_dir / "gaia.log") as logs:
        assert "Pausing Gaia ..." in logs
    assert engine.started
    assert not engine.running
    assert engine.paused
    assert not engine.stopping
    assert not engine.stopped
    with pytest.raises(RuntimeError):
        engine.pause()

    await engine.resume()
    with get_logs_content(engine.config.logs_dir / "gaia.log") as logs:
        assert "Resuming Gaia ..." in logs
    assert engine.started
    assert engine.running
    assert not engine.paused
    assert not engine.stopping
    assert not engine.stopped
    with pytest.raises(RuntimeError):
        await engine.resume()

    engine.stop()
    await engine.shutdown()
    with get_logs_content(engine.config.logs_dir / "gaia.log") as logs:
        assert "Shutting down Gaia ..." in logs
    assert not engine.started
    assert not engine.running
    assert not engine.paused
    assert not engine.stopping
    assert engine.stopped
    with pytest.raises(RuntimeError):
        await engine.resume()


@pytest.mark.asyncio
async def test_engine_run(engine: Engine):
    task = create_task(engine.run())

    await sleep(0.5)  # Allow to set up and start up
    with get_logs_content(engine.config.logs_dir / "gaia.log") as logs:
        assert "Starting Gaia ..." in logs

    engine._handle_stop_signal()

    await wait_for(task, 1.0)  # Allow to shut down


@pytest.mark.asyncio
async def test_ecosystem_managements(engine: Engine, ecosystem_config: EcosystemConfig):
    # /!\ Ecosystem need a runnable subroutine in order to start
    ecosystem_config.set_management("light", True)

    engine.init_ecosystem(ecosystem_uid)
    with get_logs_content(engine.config.logs_dir / "gaia.log") as logs:
        assert f"Ecosystem {ecosystem_uid} has been created" in logs
    with pytest.raises(RuntimeError, match=r"Ecosystem .* already exists"):
        engine.init_ecosystem(ecosystem_uid)
    with pytest.raises(RuntimeError, match=r"Cannot stop Ecosystem .*"):
        await engine.stop_ecosystem(ecosystem_uid)

    await engine.start_ecosystem(ecosystem_uid)
    with get_logs_content(engine.config.logs_dir / "gaia.log") as logs:
        assert f"Starting ecosystem {ecosystem_uid}" in logs
    with pytest.raises(RuntimeError, match=r"Ecosystem .* is already running"):
        await engine.start_ecosystem(ecosystem_uid)
    with pytest.raises(RuntimeError, match=r"Cannot dismount a started ecosystem."):
        await engine.dismount_ecosystem(ecosystem_uid)

    await engine.stop_ecosystem(ecosystem_uid)
    with get_logs_content(engine.config.logs_dir / "gaia.log") as logs:
        assert f"Ecosystem {ecosystem_uid} has been stopped" in logs
    with pytest.raises(RuntimeError, match=r"Cannot stop Ecosystem .*"):
        await engine.stop_ecosystem(ecosystem_uid)

    await engine.dismount_ecosystem(ecosystem_uid)
    with get_logs_content(engine.config.logs_dir / "gaia.log") as logs:
        assert f"Ecosystem {ecosystem_uid} has been dismounted" in logs
    with pytest.raises(RuntimeError, match=r"Need to initialise Ecosystem .* first"):
        await engine.start_ecosystem(ecosystem_uid)


@pytest.mark.asyncio
async def test_refresh_ecosystems_lighting_hours(engine: Engine):
    # Simply dispatches work to `EngineConfig` and `Ecosystem`, methods are
    #  tested there
    engine.config._sun_times = {
        "home": {"last_update": date.today(), "data": sun_times}
    }
    await engine.refresh_ecosystems_lighting_hours()
    with get_logs_content(engine.config.logs_dir / "gaia.log") as logs:
        assert "Refreshing ecosystems lighting hours" in logs


@pytest.mark.asyncio
async def test_refresh_chaos(engine: Engine):
    # Simply dispatches work to `EcosystemConfig` and `EngineConfig`, methods are
    #  tested there
    await engine.update_chaos_time_window()
    with get_logs_content(engine.config.logs_dir / "gaia.log") as logs:
        assert "Updating ecosystems chaos time window" in logs
