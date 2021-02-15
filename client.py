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
    def default(self, obj) -> None:
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
    def connect(self, *args, **kwargs) -> None:
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
    def __init__(self, engines_dict: dict, namespace=None) -> None:
        super(gaiaNamespace, self).__init__(namespace=namespace)
        self.engines = engines_dict

    def on_connect(self) -> None:
        self.on_register()

    def on_register(self) -> None:
        self.emit("register_manager", data={"uid": hex(uuid.getnode())[2:]})

    def on_disconnect(self) -> None:
        socketio_logger.info('disconnected from server')

    def on_ping(self) -> None:
        pong = []
        for engine in self.engines:
            pong.append(self.engines[engine].uid)
        self.emit("pong", data=pong)

    def on_send_config(self) -> None:
        config = {ecosystem_id: self.engines[ecosystem_id].config
                  for ecosystem_id in self.engines}
        self.emit("config", config, )

    def on_send_sensors_data(self) -> None:
        sensors_data = {}
        for ecosystem_id in self.engines:
            try:
                data = self.engines[ecosystem_id].sensors_data
                if data:
                    sensors_data[ecosystem_id] = data
            # Except when subroutines are still loading
            except KeyError:
                pass
        self.emit("sensors_data", sensors_data)

    def on_send_health_data(self) -> None:
        health_data = {}
        for ecosystem_id in self.engines:
            try:
                data = self.engines[ecosystem_id].plants_health
                if data:
                    health_data[ecosystem_id] = data
            # Except when subroutines are still loading
            except KeyError:
                pass
        self.emit("health_data", health_data)

    def on_send_light_data(self, ecosystem_uid: str = None) -> None:
        light_data = {}

        if ecosystem_uid:
            ecosystem_uids = [ecosystem_uid]
        else:
            ecosystem_uids = [e_uid for e_uid in self.engines.keys()]

        for e_uid in ecosystem_uids:
            try:
                data = self.engines[e_uid].light_info
                if data:
                    light_data[e_uid] = data
            # Except when subroutines are still loading
            except KeyError:
                pass
        self.emit("light_data", light_data)

    def on_turn_light_on(self, message: dict) -> None:
        ecosystem_uid = message["ecosystem"]
        countdown = message["countdown"]
        try:
            self.engines[ecosystem_uid].set_light_on(countdown=countdown)
            self.on_send_light_data(ecosystem_uid)
        # Except when subroutines are still loading
        except KeyError:
            print(f"{ecosystem_uid}'s light subroutine has not initialized yet")

    def on_turn_light_off(self, message: dict) -> None:
        ecosystem_uid = message["ecosystem"]
        countdown = message["countdown"]
        try:
            self.engines[ecosystem_uid].set_light_off(countdown=countdown)
            self.on_send_light_data(ecosystem_uid)
        # Except when subroutines are still loading
        except KeyError:
            print(f"{ecosystem_uid}'s light subroutine has not initialized yet")

    def on_turn_light_auto(self, message: dict) -> None:
        ecosystem_uid = message["ecosystem"]
        try:
            self.engines[ecosystem_uid].set_light_auto()
            self.on_send_light_data(ecosystem_uid)
        # Except when subroutines are still loading
        except KeyError:
            print(f"{ecosystem_uid}'s light subroutine has not initialized yet")
