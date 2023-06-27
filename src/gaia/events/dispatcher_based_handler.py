try:
    import dispatcher
except ImportError:
    raise RuntimeError(
        "Event-dispatcher is required to use the event dispatcher. Download it "
        "from `https://gitlab.com/eupla/dispatcher` and install it in your "
        "virtual env"
    )

from gaia.events import Events
from gaia.engine import Engine


class DispatcherBasedGaiaEvents(dispatcher.EventHandler, Events):
    """A Dispatcher EventHandler using the events defined by the Events class
    """
    type = "dispatcher"

    def __init__(self, namespace: str, engine: Engine):
        super().__init__(namespace=namespace, engine=engine)
