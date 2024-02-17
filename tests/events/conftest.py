import pytest

from gaia import Ecosystem, Engine
from gaia.events import Events

from ..utils import MockDispatcher


@pytest.fixture(scope="module")
def mock_dispatcher():
    mock_dispatcher = MockDispatcher("gaia")
    return mock_dispatcher


@pytest.fixture(scope="function")
def events_handler(
        mock_dispatcher: MockDispatcher,
        engine: Engine,
        ecosystem: Ecosystem,
):
    events_handler = Events(engine)
    mock_dispatcher.register_event_handler(events_handler)
    engine.message_broker = mock_dispatcher
    engine.event_handler = events_handler

    try:
        yield events_handler
    finally:
        mock_dispatcher.clear_store()
