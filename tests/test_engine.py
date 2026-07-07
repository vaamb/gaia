from asyncio import create_task, wait_for
from datetime import date
from unittest.mock import patch

import pytest

from dispatcher import AsyncEventHandler, AsyncDispatcher
from sqlalchemy_wrapper import AsyncSQLAlchemyWrapper

from gaia import EcosystemConfig, Engine, EngineConfig

from tests import data as test_data
from tests.utils import yield_control


@pytest.mark.asyncio
class TestEngine:
    def test_singleton(self, engine: Engine, engine_config: EngineConfig):
        assert engine is Engine(engine_config)

    async def test_engine_dict(self, engine: Engine, engine_config: EngineConfig):
        assert engine.config.__dict__ == engine_config.__dict__

    async def test_plugins_needed(self, engine: Engine):
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

    @pytest.mark.timeout(10)
    async def test_message_broker(
            self,
            engine: Engine,
            caplog: pytest.LogCaptureFixture,
    ):
        # Test when communication is disabled in config
        engine.config.app_config.COMMUNICATE_WITH_OURANOS = False
        assert engine.use_message_broker is False

        with pytest.raises(RuntimeError, match="COMMUNICATE_WITH_OURANOS"):
            await engine.init_message_broker()
        with pytest.raises(AttributeError):
            assert isinstance(engine.message_broker, AsyncDispatcher)
        with pytest.raises(AttributeError):
            assert isinstance(engine.event_handler, AsyncEventHandler)

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
        assert "Initialising the event dispatcher" in caplog.text

        assert engine._message_broker is not None
        assert isinstance(engine.message_broker, AsyncDispatcher)
        assert isinstance(engine.event_handler, AsyncEventHandler)

        # Test message broker start and stop
        with patch.object(engine.message_broker._connected, "is_set", return_value=True):
            await engine.start_message_broker()
            # Give time for the message broker to start
            await yield_control()
            assert engine.message_broker_started
            await engine.stop_message_broker()
            assert not engine.message_broker_started

    async def test_database(self, engine: Engine, caplog: pytest.LogCaptureFixture):
        # Test when DB is disabled in config
        engine.config.app_config.USE_DATABASE = False
        assert engine.use_db is False

        with pytest.raises(RuntimeError, match="USE_DATABASE"):
            await engine.init_database()
        assert engine.use_db is False
        with pytest.raises(AttributeError):
            assert isinstance(engine.db, AsyncSQLAlchemyWrapper)

        # Test DB initialization
        engine.config.app_config.USE_DATABASE = True
        assert engine.use_db is True

        await engine.init_database()
        assert "Initialising the database" in caplog.text
        assert isinstance(engine.db, AsyncSQLAlchemyWrapper)

        # Test DB start and stop
        await engine.start_database()
        assert engine.db_started
        await engine.stop_database()
        assert not engine.db_started

    @pytest.mark.timeout(10)
    async def test_plugins(self, engine: Engine, caplog: pytest.LogCaptureFixture):
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
        assert "Initialising the plugins" in caplog.text
        assert engine.plugins_initialized is True

        await engine.start_plugins()
        await engine.stop_plugins()

        # Reset the message broker and the database
        engine.message_broker = None
        engine.event_handler = None
        engine.db = None

    async def test_background_tasks(
            self,
            engine: Engine,
            caplog: pytest.LogCaptureFixture,
    ):
        engine.start_background_tasks()
        assert "Starting the background tasks" in caplog.text
        engine.stop_background_tasks()
        assert "Stopping the background tasks" in caplog.text

    async def test_states(self, engine: Engine, caplog: pytest.LogCaptureFixture):
        assert not engine.started
        assert not engine.running
        assert not engine.paused
        assert not engine.stopped
        assert not engine.terminated

        await engine.start()
        assert "Starting Gaia ..." in caplog.text
        assert engine.started
        assert engine.running
        assert not engine.paused
        assert not engine.stopped
        assert not engine.terminated
        with pytest.raises(RuntimeError):
            await engine.resume()

        engine.pause()
        assert "Pausing Gaia ..." in caplog.text
        assert engine.started
        assert not engine.running
        assert engine.paused
        assert not engine.stopped
        assert not engine.terminated
        with pytest.raises(RuntimeError):
            engine.pause()

        await engine.resume()
        assert "Resuming Gaia ..." in caplog.text
        assert engine.started
        assert engine.running
        assert not engine.paused
        assert not engine.stopped
        assert not engine.terminated
        with pytest.raises(RuntimeError):
            await engine.resume()

        await engine.stop()
        assert "Stopping Gaia ..." in caplog.text
        await engine.terminate()
        assert "Terminating Gaia ..." in caplog.text
        assert not engine.started
        assert not engine.running
        assert not engine.paused
        assert not engine.stopped
        assert engine.terminated
        with pytest.raises(RuntimeError):
            await engine.resume()

    async def test_run(self, engine: Engine, caplog: pytest.LogCaptureFixture):
        task = create_task(engine.run())

        await yield_control()  # Allow to set up and start up
        assert "Starting Gaia ..." in caplog.text

        engine._handle_stop_signal()

        await wait_for(task, 1.0)  # Allow to shut down

    @pytest.mark.timeout(10)
    async def test_loop_survives_refresh_error(
            self,
            engine: Engine,
            caplog: pytest.LogCaptureFixture,
    ):
        await engine.start()

        caplog.clear()
        with patch.object(
                engine, "refresh_ecosystems", side_effect=RuntimeError("Oops")):
            await engine._notify_loop()
        assert "Encountered an error while refreshing the ecosystems." in caplog.messages[0]

        # The loop should still be alive and react to the next config change
        caplog.clear()
        assert not engine.task.done()
        await engine._notify_loop()
        assert "Refreshing the ecosystems ..." in caplog.messages[0]

        await engine.stop()
        await engine.terminate()

    async def test_ecosystem_managements(
            self,
            engine: Engine,
            ecosystem_config: EcosystemConfig,
            caplog: pytest.LogCaptureFixture,
    ):
        # Ecosystems are initialized during the engine initialization
        await engine.terminate_ecosystems()
        # /!\ Ecosystem need a runnable subroutine in order to start
        ecosystem_config.set_management("light", True)

        await engine.add_ecosystem(test_data.ecosystem_uid)
        assert f"Ecosystem {test_data.ecosystem_uid} has been created" in caplog.text
        with pytest.raises(RuntimeError, match=r"Ecosystem .* already exists"):
            await engine.add_ecosystem(test_data.ecosystem_uid)
        with pytest.raises(RuntimeError, match=r"Ecosystem .* is not running"):
            await engine.stop_ecosystem(test_data.ecosystem_uid)

        await engine.start_ecosystem(test_data.ecosystem_uid)
        assert f"Starting ecosystem {test_data.ecosystem_uid}" in caplog.text
        with pytest.raises(RuntimeError, match=r"Ecosystem .* is already running"):
            await engine.start_ecosystem(test_data.ecosystem_uid)
        with pytest.raises(RuntimeError, match=r"Cannot dismount a started ecosystem."):
            await engine.remove_ecosystem(test_data.ecosystem_uid)

        await engine.stop_ecosystem(test_data.ecosystem_uid)
        assert f"Ecosystem {test_data.ecosystem_uid} has been stopped" in caplog.text
        with pytest.raises(RuntimeError, match=r"Ecosystem .* is not running"):
            await engine.stop_ecosystem(test_data.ecosystem_uid)

        await engine.remove_ecosystem(test_data.ecosystem_uid)
        assert f"Ecosystem {test_data.ecosystem_uid} has been dismounted" in caplog.text
        with pytest.raises(ValueError, match=r"Ecosystem .* is not linked to this engine"):
            await engine.start_ecosystem(test_data.ecosystem_uid)

    async def test_refresh_ecosystems_lighting_hours(
            self,
            engine: Engine,
            caplog: pytest.LogCaptureFixture,
    ):
        # Simply dispatches work to `EngineConfig` and `Ecosystem`, methods are
        #  tested there
        engine.config._sun_times = {
            "home": {"last_update": date.today(), "data": test_data.sun_times}
        }
        await engine.refresh_ecosystems_lighting_hours()
        assert "Refreshing ecosystems lighting hours" in caplog.text

    async def test_refresh_chaos(self, engine: Engine, caplog: pytest.LogCaptureFixture):
        # Simply dispatches work to `EcosystemConfig` and `EngineConfig`, methods are
        #  tested there
        await engine.update_chaos_time_window()
        assert "Updating ecosystems chaos time window" in caplog.text
