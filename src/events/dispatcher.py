from dispatcher import EventHandler

from . import Events
from ..ecosystem import Ecosystem


class gaiaEvents(EventHandler, Events):
    """A Dispatcher EventHandler using the events defined by the Events class
    """
    def __init__(self, ecosystem_dict: dict[str, Ecosystem]):
        # Dirty but it works
        EventHandler.__init__(self)
        Events.__init__(self, ecosystem_dict)
