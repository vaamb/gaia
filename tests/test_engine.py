import pytest

from dispatcher import EventHandler, KombuDispatcher
from sqlalchemy_wrapper import SQLAlchemyWrapper

from gaia import EcosystemConfig, Engine, EngineConfig

from .data import ecosystem_uid, sun_times
from .utils import get_logs_content


def test_engine_singleton(engine: Engine, engine_config: EngineConfig):
    assert engine is Engine(engine_config)


def test_engine_dict(engine: Engine, engine_config: EngineConfig):
    assert engine.config.__dict__ == engine_config.__dict__


def test_engine_message_broker(engine: Engine):
    assert engine.config.app_config.COMMUNICATE_WITH_OURANOS is False
    assert engine.use_message_broker is False
    assert engine.plugins_needed is False

    with pytest.raises(RuntimeError, match="COMMUNICATE_WITH_OURANOS"):
        engine.init_message_broker()
    with pytest.raises(AttributeError):
        assert isinstance(engine.message_broker, KombuDispatcher)
    with pytest.raises(AttributeError):
        assert isinstance(engine.event_handler, EventHandler)

    engine.config.app_config.COMMUNICATE_WITH_OURANOS = True
    assert engine.plugins_needed is True

    url = engine.config.app_config.AGGREGATOR_COMMUNICATION_URL

    engine.config.app_config.AGGREGATOR_COMMUNICATION_URL = None
    with pytest.raises(RuntimeError, match="AGGREGATOR_COMMUNICATION_URL"):
        engine.init_message_broker()

    engine.config.app_config.AGGREGATOR_COMMUNICATION_URL = "Invalid"
    with pytest.raises(ValueError, match="is not a valid broker URL"):
        engine.init_message_broker()

    engine.config.app_config.AGGREGATOR_COMMUNICATION_URL = "Invalid://"
    with pytest.raises(ValueError, match="is not supported"):
        engine.init_message_broker()

    engine.config.app_config.AGGREGATOR_COMMUNICATION_URL = url
    engine.init_message_broker()
    with get_logs_content(engine.config.logs_dir / "base.log") as logs:
        assert "Initialising the event dispatcher" in logs

    assert engine.use_message_broker is True
    assert isinstance(engine.message_broker, KombuDispatcher)
    assert isinstance(engine.event_handler, EventHandler)

    engine.start_message_broker()
    engine.stop_message_broker()


def test_engine_database(engine: Engine):
    assert engine.config.app_config.USE_DATABASE is False
    assert engine.use_db is False
    assert engine.plugins_needed is False

    with pytest.raises(RuntimeError, match="USE_DATABASE"):
        engine.init_database()
    assert engine.use_db is False
    with pytest.raises(AttributeError):
        assert isinstance(engine.db, SQLAlchemyWrapper)

    engine.config.app_config.USE_DATABASE = True
    assert engine.plugins_needed is True

    engine.init_database()
    with get_logs_content(engine.config.logs_dir / "base.log") as logs:
        assert "Initialising the database" in logs
    assert engine.use_db is True
    assert isinstance(engine.db, SQLAlchemyWrapper)

    engine.start_database()
    engine.stop_database()


def test_engine_plugins(engine: Engine):
    assert engine.config.app_config.COMMUNICATE_WITH_OURANOS is False
    assert engine.config.app_config.USE_DATABASE is False
    assert engine.plugins_needed is False

    with pytest.raises(RuntimeError):
        engine.init_plugins()
    with pytest.raises(RuntimeError):
        engine.start_plugins()

    engine.config.app_config.COMMUNICATE_WITH_OURANOS = True
    engine.config.app_config.USE_DATABASE = True
    assert engine.plugins_needed is True

    engine.init_plugins()
    with get_logs_content(engine.config.logs_dir / "base.log") as logs:
        assert "Initialising the plugins" in logs
    assert engine.plugins_initialized is True

    engine.start_plugins()
    engine.stop_plugins()


def test_engine_background_tasks(engine: Engine):
    engine.start_background_tasks()
    with get_logs_content(engine.config.logs_dir / "base.log") as logs:
        assert "Starting the background tasks" in logs
    engine.stop_background_tasks()
    with get_logs_content(engine.config.logs_dir / "base.log") as logs:
        assert "Stopping the background tasks" in logs


def test_engine_states(engine: Engine):
    assert not engine.started
    assert not engine.running
    assert not engine.paused
    assert not engine.stopping
    assert not engine.stopped

    engine.start()
    with get_logs_content(engine.config.logs_dir / "base.log") as logs:
        assert "Starting Gaia ..." in logs
    assert engine.started
    assert engine.running
    assert not engine.paused
    assert not engine.stopping
    assert not engine.stopped
    with pytest.raises(RuntimeError):
        engine.resume()

    engine.pause()
    with get_logs_content(engine.config.logs_dir / "base.log") as logs:
        assert "Pausing Gaia ..." in logs
    assert engine.started
    assert not engine.running
    assert engine.paused
    assert not engine.stopping
    assert not engine.stopped
    with pytest.raises(RuntimeError):
        engine.pause()

    engine.resume()
    with get_logs_content(engine.config.logs_dir / "base.log") as logs:
        assert "Resuming Gaia ..." in logs
    assert engine.started
    assert engine.running
    assert not engine.paused
    assert not engine.stopping
    assert not engine.stopped
    with pytest.raises(RuntimeError):
        engine.resume()

    engine.stop()
    with get_logs_content(engine.config.logs_dir / "base.log") as logs:
        assert "Shutting down Gaia ..." in logs
    assert not engine.started
    assert not engine.running
    assert not engine.paused
    assert not engine.stopping
    assert engine.stopped
    with pytest.raises(RuntimeError):
        engine.resume()


def test_ecosystem_managements(engine: Engine, ecosystem_config: EcosystemConfig):
    # /!\ Ecosystem need a runnable subroutine in order to start
    ecosystem_config.set_management("light", True)

    engine.init_ecosystem(ecosystem_uid)
    with get_logs_content(engine.config.logs_dir / "base.log") as logs:
        assert f"Ecosystem {ecosystem_uid} has been created" in logs
    with pytest.raises(RuntimeError, match=r"Ecosystem .* already exists"):
        engine.init_ecosystem(ecosystem_uid)
    with pytest.raises(RuntimeError, match=r"Cannot stop Ecosystem .*"):
        engine.stop_ecosystem(ecosystem_uid)

    engine.start_ecosystem(ecosystem_uid)
    with get_logs_content(engine.config.logs_dir / "base.log") as logs:
        assert f"Starting ecosystem {ecosystem_uid}" in logs
    with pytest.raises(RuntimeError, match=r"Ecosystem .* is already running"):
        engine.start_ecosystem(ecosystem_uid)
    with pytest.raises(RuntimeError, match=r"Cannot dismount a started ecosystem."):
        engine.dismount_ecosystem(ecosystem_uid)

    engine.stop_ecosystem(ecosystem_uid)
    with get_logs_content(engine.config.logs_dir / "base.log") as logs:
        assert f"Ecosystem {ecosystem_uid} has been stopped" in logs
    with pytest.raises(RuntimeError, match=r"Cannot stop Ecosystem .*"):
        engine.stop_ecosystem(ecosystem_uid)

    engine.dismount_ecosystem(ecosystem_uid)
    with get_logs_content(engine.config.logs_dir / "base.log") as logs:
        assert f"Ecosystem {ecosystem_uid} has been dismounted" in logs
    with pytest.raises(RuntimeError, match=r"Need to initialise Ecosystem .* first"):
        engine.start_ecosystem(ecosystem_uid)


def test_refresh_sun_times(engine: Engine):
    # Simply dispatches work to `EngineConfig` and `Ecosystem`, methods are
    #  tested there
    engine.config._sun_times = sun_times
    engine.refresh_sun_times()
    with get_logs_content(engine.config.logs_dir / "base.log") as logs:
        assert "Refreshing ecosystems sun times" in logs


def test_refresh_chaos(engine: Engine):
    # Simply dispatches work to `EcosystemConfig` and `EngineConfig`, methods are
    #  tested there
    engine.update_chaos_time_window()
    with get_logs_content(engine.config.logs_dir / "base.log") as logs:
        assert "Updating ecosystems chaos time window" in logs
