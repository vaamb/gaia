import random

import socketio
from socketio import exceptions
from socketio.client import reconnecting_clients

from . import Events, logger
from ..ecosystem import Ecosystem


class RetryClient(socketio.Client):
    """A Socket.IO client that retries to connect to a Socket.IO server even if
    if could not reach it first.
    """
    def __init__(self, *args, **kwargs):
        logger.debug("Starting socketIO client")
        super().__init__(*args, **kwargs)

    def connect(self, *args, **kwargs) -> None:
        logger.info("Attempting to connect to the server")
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
    def __init__(self, ecosystem_dict: dict[str, Ecosystem], namespace):
        # Dirty but it works
        socketio.ClientNamespace.__init__(self, namespace=namespace)
        Events.__init__(self, ecosystem_dict)
