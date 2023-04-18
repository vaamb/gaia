from __future__ import annotations

from datetime import datetime
import logging
from threading import Thread
from time import sleep
import typing as t
from typing import Type

from gaia_validators import (
    BaseInfoConfigPayload, BrokerPayload, Empty, EnvironmentConfigPayload,
    HardwareConfigPayload, HealthDataPayload, LightDataPayload,
    ManagementConfigPayload, SensorsDataPayload
)

from gaia.config import get_config
from gaia.shared_resources import scheduler
from gaia.utils import (
    encrypted_uid, generate_uid_token, humanize_list, local_ip_address
)


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.ecosystem import Ecosystem


if get_config().USE_DATABASE:
    from sqlalchemy import select
    from sqlalchemy_wrapper import SQLAlchemyWrapper

    from gaia.database.models import SensorHistory


payload_classes: dict[str, Type[BrokerPayload]] = {
    "base_info": BaseInfoConfigPayload,
    "management": ManagementConfigPayload,
    "environmental_parameters": EnvironmentConfigPayload,
    "hardware": HardwareConfigPayload,
    "sensors_data": SensorsDataPayload,
    "health_data": HealthDataPayload,
    "light_data": LightDataPayload,
}


attr_for_event: dict[str, str] = {
    "base_info": "base_info",
    "management": "management",
    "environmental_parameters": "environmental_parameters",
    "hardware": "hardware",
    "sensors_data": "sensors_data",
    "health_data": "plants_health",
    "light_data": "light_info",
}


class Events:
    """A class holding all the events coming from either socketio or
    event-dispatcher

    :param ecosystem_dict: a dict holding all the Ecosystem instances
    """
    type = "raw"

    def __init__(self, ecosystem_dict: dict[str, "Ecosystem"], **kwargs) -> None:
        super().__init__(**kwargs)
        self.ecosystems = ecosystem_dict
        self._registered = False
        self._background_task = False
        self._thread: Thread | None = None
        self.logger = logging.getLogger(f"gaia.broker")
        self.db: "SQLAlchemyWrapper" | None
        if get_config().USE_DATABASE:
            from gaia.database.models import db
            self.db = db
            self.db.init(get_config())
            self.db.create_all()
        else:
            self.db = None

    def emit(self, event, data=None, to=None, room=None, namespace=None, **kwargs):
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    def _try_func(self, func):
        try:
            func()
        except Exception as e:
            log_msg = (
                f"Encountered an error while handling function `{func.__name__}`. "
                f"ERROR msg: `{e.__class__.__name__} :{e}`"
            )
            ex_msg = e.args[1] if len(e.args) > 1 else e.args[0]
            # If socketio error when not connected, log to debug
            if "is not a connected namespace" in ex_msg:
                self.logger.debug(log_msg)
            else:
                self.logger.error(log_msg)

    def background_task(self):
        scheduler.add_job(
            self._try_func, kwargs={"func": self.send_sensors_data},
            id="send_sensors_data", trigger="cron", minute="*",
            misfire_grace_time=10
        )
        scheduler.add_job(
            self._try_func, kwargs={"func": self.send_light_data},
            id="send_light_data", trigger="cron", hour="1",
            misfire_grace_time=10*60
        )
        scheduler.add_job(
            self._try_func, kwargs={"func": self.send_health_data},
            id="send_health_data", trigger="cron", hour="1",
            misfire_grace_time=10*60
        )
        while True:
            self.ping()
            sleep(15)

    def ping(self) -> None:
        ecosystems = [ecosystem.uid for ecosystem in self.ecosystems.values()]
        if self.type == "socketio":
            self.emit("ping", data=ecosystems)
        elif self.type == "dispatcher":
            self.emit("ping", data=ecosystems, ttl=30)

    def register(self) -> None:
        if self.type == "socketio":
            data = {"ikys": encrypted_uid(), "uid_token": generate_uid_token()}
            self.emit("register_engine", data=data)
        elif self.type == "dispatcher":
            data = {
                "engine_uid": get_config().ENGINE_UID,
                "address": local_ip_address(),
            }
            self.emit("register_engine", data=data, ttl=30)
        else:
            raise TypeError("Event type is invalid")

    def initialize_data_transfer(self) -> None:
        if not self._background_task:
            thread = Thread(target=self.background_task)
            thread.name = "ping"
            thread.start()
            self._thread = thread
            self._background_task = True
        self.send_full_config()
        self.send_sensors_data()
        self.send_light_data()
        self.send_health_data()

    def on_connect(self, environment) -> None:
        self.logger.info("Connection successful")
        self.register()

    def on_disconnect(self, *args) -> None:
        if self._registered:
            self.logger.warning("Disconnected from server")
        else:
            self.logger.error("Failed to register engine")

    def on_register(self, *args):
        self._registered = False
        self.logger.info("Received registration request from server")
        self.register()

    def on_register_ack(self, *args) -> None:
        self.logger.info("Engine registration successful")
        self._registered = True
        self.initialize_data_transfer()

    def filter_uids(self, ecosystem_uids: list[str] | None = None) -> list[str]:
        if ecosystem_uids is None:
            return [uid for uid in self.ecosystems.keys()]
        else:
            return [
                uid for uid in ecosystem_uids
                if uid in self.ecosystems.keys()
            ]

    def get_event_payload(
            self,
            event_name: str,
            ecosystem_uids: list[str] | None = None
    ) -> list[BrokerPayload]:
        rv = []
        attr_name = attr_for_event.get(event_name, None)
        if attr_name is None:
            self.logger.error(f"Payload for event {event_name} is not defined")
            return rv
        uids = self.filter_uids(ecosystem_uids)
        self.logger.debug(
            f"Getting '{event_name}' payload for {humanize_list(uids)}")
        for uid in uids:
            data = getattr(self.ecosystems[uid], attr_name)
            if not isinstance(data, Empty):
                payload_class = payload_classes[event_name]
                payload: BrokerPayload = payload_class.from_base(uid, data)
                rv.append(payload)
        return rv

    def emit_event(
            self,
            event_name: str,
            ecosystem_uids: list[str] | None = None
    ) -> None:
        self.logger.debug(f"Sending event {event_name} requested")
        payload = self.get_event_payload(event_name, ecosystem_uids)
        if payload:
            self.logger.debug(f"Payload for event {event_name} sent")
            self.emit(event_name, data=payload)
        else:
            self.logger.debug(f"No payload for event {event_name}")

    def send_full_config(
            self,
            ecosystem_uids: list[str] | None = None
    ) -> None:
        for cfg in ("base_info", "management", "environmental_parameters", "hardware"):
            self.emit_event(cfg, ecosystem_uids)

    def send_sensors_data(self, ecosystem_uids: list[str] | None = None) -> None:
        self.emit_event("sensors_data", ecosystem_uids)

    def send_health_data(self, ecosystem_uids: list[str] | None = None) -> None:
        self.emit_event("health_data", ecosystem_uids)

    def send_light_data(self, ecosystem_uids: list[str] | None = None) -> None:
        self.emit_event("light_data", ecosystem_uids)

    def on_turn_light(self, message: dict) -> None:
        message["actuator"] = "light"
        self.on_turn_actuator(message)

    def on_turn_actuator(self, message: dict) -> None:
        ecosystem_uid: str = message["ecosystem"]
        actuator: str = message["actuator"]
        mode: str = message["mode"]
        countdown: float = message.get("countdown", 0.0)
        if ecosystem_uid in self.ecosystems:
            self.logger.debug("Received turn_actuator event")
            self.ecosystems[ecosystem_uid].turn_actuator(
                actuator=actuator, mode=mode, countdown=countdown
            )
            if actuator == "light":
                self.emit_event("light_data", ecosystem_uids=[ecosystem_uid])

    def on_change_management(self, message: dict) -> None:
        ecosystem_uid: str = message["ecosystem"]
        management: str = message["management"]
        status: bool = message["status"]
        if ecosystem_uid in self.ecosystems:
            self.ecosystems[ecosystem_uid].config.set_management(management, status)
            self.ecosystems[ecosystem_uid].config.save()
            self.emit_event("management", ecosystem_uids=[ecosystem_uid])

    def on_get_data_since(self, message: dict) -> None:
        if self.db is not None:
            ecosystem_uids: list[str] = message["ecosystems"]
            uids: list[str] = self.filter_uids(ecosystem_uids)
            since_str: str = message["since"]
            since: datetime = datetime.fromisoformat(since_str).astimezone()
            with self.db.scoped_session() as session:
                query = (
                    select(SensorHistory)
                    .where(SensorHistory.timestamp >= since)
                    .where(SensorHistory.ecosystem_uid.in_(uids))
                )
                results = session.execute(query).all().scalars()
            self.emit(
                "sensor_data_record",
                [result.dict_repr for result in results]
            )
        else:
            self.logger.error(
                "Received 'get_data_since' event but USE_DATABASE is set to False"
            )
            return
