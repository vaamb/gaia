from datetime import date, datetime, time
import json
import logging
import random
import uuid

import pytz
from tzlocal import get_localzone

import socketio
from socketio import exceptions
from socketio.client import reconnecting_clients


class datetimeJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            obj = obj.astimezone(tz=pytz.timezone("UTC"))
            return obj.replace(microsecond=0).isoformat()
        if isinstance(obj, time):
            obj = datetime.combine(date.today(), obj)
            obj = obj.astimezone(tz=get_localzone())
            obj = obj.astimezone(tz=pytz.timezone("UTC")).time()
            return obj.replace(microsecond=0).isoformat()


json.JSONEncoder = datetimeJSONEncoder


socketio_logger = logging.getLogger("socketio.client")


class retryClient(socketio.Client):
    def connect(self, *args, **kwargs):
        self._reconnect_abort.clear()
        reconnecting_clients.append(self)
        attempt_count = 0
        current_delay = self.reconnection_delay
        while True:
            try:
                super().connect(*args, **kwargs)
            except (socketio.exceptions.ConnectionError, ValueError):
                pass
            else:
                self.logger.info('Connection successful')
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
            if self._reconnect_abort.wait(delay):
                self.logger.info('Reconnect task aborted')
                break
            attempt_count += 1

            if self.reconnection_attempts and \
                    attempt_count >= self.reconnection_attempts:
                self.logger.info(
                    'Maximum reconnection attempts reached, giving up')
                break
        reconnecting_clients.remove(self)


class gaiaNamespace(socketio.ClientNamespace):
    def __init__(self, engines_dict, namespace=None):
        super(gaiaNamespace, self).__init__(namespace=namespace)
        self.engines = engines_dict

    def on_connect(self):
        self.on_register()

    def on_register(self):
        self.emit("register_manager", data={"uid": hex(uuid.getnode())[2:]})

    def on_disconnect(self):
        socketio_logger.info('disconnected from server')

    def on_send_config(self):
        config = {ecosystem_id: self.engines[ecosystem_id].config_dict
                  for ecosystem_id in self.engines}
        self.emit("config", config, )

    def on_send_sensors_data(self):
        data = {}
        for ecosystem_id in self.engines:
            try:
                data[ecosystem_id] = self.engines[ecosystem_id].sensors_data
            except RuntimeError:
                continue
        self.emit("sensors_data", data)

    def on_send_health_data(self):
        data = {}
        for ecosystem_id in self.engines:
            try:
                health = self.engines[ecosystem_id].plants_health
                if health:
                    data[ecosystem_id] = health
            except RuntimeError:
                continue
        self.emit("health_data", data)

    def on_send_light_data(self):
        data = {}
        for ecosystem_id in self.engines:
            try:
                data[ecosystem_id] = self.engines[ecosystem_id].light_info
            except RuntimeError:
                continue
        self.emit("light_data", data)

    def on_turn_light_on(self, message):
        ecosystem = message["ecosystem"]
        countdown = message["countdown"]
        self.engines[ecosystem].set_light_on(countdown=countdown)
        self.on_send_light_data()

    def on_turn_light_off(self, message):
        ecosystem = message["ecosystem"]
        countdown = message["countdown"]
        self.engines[ecosystem].set_light_off(countdown=countdown)
        self.on_send_light_data()

    def on_turn_light_auto(self, message):
        ecosystem = message["ecosystem"]
        self.engines[ecosystem].set_light_off()
        self.on_send_light_data()
