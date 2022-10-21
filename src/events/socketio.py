from __future__ import annotations

import logging
import random
from threading import Event

try:
    import socketio
except ImportError:
    raise RuntimeError(
        "Python-socketio and websocket-client are required "
        "to use socketio. Run `pip install python-socketio[client] "
        "websocket-client` in your virtual env"
    )
else:
    from socketio.exceptions import BadNamespaceError
    from socketio.client import reconnecting_clients

from . import Events
from config import Config
from src.ecosystem import Ecosystem


class RetryClient(socketio.Client):
    """A Socket.IO client that retries to connect to a Socket.IO server even if
    if could not reach it first.
    """
    def __init__(self, *args, **kwargs):
        self.logger = logging.getLogger(f"{Config.APP_NAME.lower()}.socketio")
        self.logger.debug("Starting socketIO client")
        super().__init__(*args, **kwargs)
        self.is_socketio = True  # Used by Gaia to choose the sleep method
        self._connect_event: Event | None

    def connect(self, *args, **kwargs) -> None:
        self.logger.info("Attempting to connect to the server")
        reconnecting_clients.append(self)
        attempt_count = 0
        current_delay = self.reconnection_delay
        if self._connect_event is None:
            self._connect_event = self.eio.create_event()
        else:
            self._connect_event.clear()
        while True:
            try:
                super().connect(*args, **kwargs)
            except (socketio.exceptions.ConnectionError, ValueError):
                pass
            else:
                self._reconnect_task = None
                break

            delay = current_delay
            current_delay *= 2
            if delay > self.reconnection_delay_max:
                delay = self.reconnection_delay_max
            delay += self.randomization_factor * (2 * random.random() - 1)
            self.logger.info(
                'Connection failed, new attempt in {:.02f} seconds'.format(
                    delay))
            if self._connect_event.wait(delay):
                self.logger.info('Reconnect task aborted')
                break
            attempt_count += 1

            if self.reconnection_attempts and \
                    attempt_count >= self.reconnection_attempts:
                self.logger.info(
                    'Maximum reconnection attempts reached, giving up')
                break
        reconnecting_clients.remove(self)


class gaiaNamespace(socketio.ClientNamespace, Events):
    """A Socket.IO client namespace using the events defined by the Events class
    """
    type = "socketio"

    def __init__(self, namespace: str, ecosystem_dict: dict[str, Ecosystem]):
        super().__init__(namespace=namespace, ecosystem_dict=ecosystem_dict)
