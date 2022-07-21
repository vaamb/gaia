try:
    import dispatcher
except ImportError:
    raise RuntimeError(
        "Event-dispatcher is required to use the event dispatcher. Download it "
        "from `https://gitlab.com/eupla/dispatcher` and install it in your "
        "virtual env"
    )

from dispatcher import get_dispatcher  # used by gaia

from . import Events
from ..ecosystem import Ecosystem


class gaiaEvents(dispatcher.EventHandler, Events):
    """A Dispatcher EventHandler using the events defined by the Events class
    """
    def __init__(self, ecosystem_dict: dict[str, Ecosystem]):
        # Dirty but it works
        dispatcher.EventHandler.__init__(self)
        Events.__init__(self, ecosystem_dict)
