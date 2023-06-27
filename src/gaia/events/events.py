from __future__ import annotations

from datetime import datetime
import logging
import inspect
from threading import Thread
from time import sleep
import typing as t
from typing import Callable, Literal, Type
import weakref

from pydantic import BaseModel, ValidationError

from gaia_validators import *

from gaia.config import get_config, get_environment_config
from gaia.shared_resources import scheduler
from gaia.utils import (
    encrypted_uid, generate_uid_token, humanize_list, local_ip_address)

if get_config().USE_DATABASE:
    from sqlalchemy import select
    from sqlalchemy_wrapper import SQLAlchemyWrapper

    from gaia.database.models import SensorHistory


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.ecosystem import Ecosystem
    from gaia.engine import Engine


EventNames = Literal[
    "base_info", "management", "environmental_parameters", "hardware",
    "sensors_data", "health_data", "light_data", "actuator_data"]


payload_classes: dict[EventNames, Type[EcosystemPayload]] = {
    "base_info": BaseInfoConfigPayload,
    "management": ManagementConfigPayload,
    "environmental_parameters": EnvironmentConfigPayload,
    "hardware": HardwareConfigPayload,
    "sensors_data": SensorsDataPayload,
    "health_data": HealthDataPayload,
    "light_data": LightDataPayload,
    "actuator_data": ActuatorsDataPayload,
}


class Events:
    """A class holding all the events coming from either socketio or
    event-dispatcher

    :param engine: an `Engine` instance
    """
    type = "raw"

    def __init__(self, engine: "Engine", **kwargs) -> None:
        super().__init__(**kwargs)
        self.engine: "Engine" = weakref.proxy(engine)
        self.ecosystems: dict[str, "Ecosystem"] = self.engine.ecosystems
        self.registered = False
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

    def validate_payload(
            self,
            data: dict,
            model_cls: Type[BaseModel],
    ) -> dict:
        if not data:
            event = inspect.stack()[1].function.lstrip("on_")
            self.logger.error(
                f"Encountered an error while validating '{event}' data. Error "
                f"msg: Empty data."
            )
            raise ValidationError
        try:
            return model_cls(**data).dict()
        except ValidationError as e:
            event = inspect.stack()[1].function.lstrip("on_")
            msg_list = [f"{error['loc'][0]}: {error['msg']}" for error in e.errors()]
            self.logger.error(
                f"Encountered an error while validating '{event}' data. Error "
                f"msg: {', '.join(msg_list)}"
            )
            raise

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

    def start_background_task(self):
        if not self._background_task:
            thread = Thread(target=self.background_task)
            thread.name = "ping"
            thread.start()
            self._thread = thread
            self._background_task = True

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
            data = EnginePayload(
                engine_uid=get_config().ENGINE_UID,
                address=local_ip_address(),
            )
            self.emit("register_engine", data=data, ttl=2)
        else:
            raise TypeError("Event type is invalid")

    def send_ecosystems_info(
            self,
            ecosystem_uids: str | list[str] | None = None
    ) -> None:
        uids = self.filter_uids(ecosystem_uids)
        self.send_full_config(uids)
        self.send_sensors_data(uids)
        self.send_actuator_data(uids)
        self.send_light_data(uids)
        self.send_health_data(uids)

    def on_connect(self, environment) -> None:  # noqa
        if self.type == "socketio":
            self.logger.info("Connection to Ouranos successful")
        elif self.type == "dispatcher":
            self.logger.info("Connection to dispatcher successful")
        else:
            raise TypeError("Event type is invalid")
        self.register()

    def on_disconnect(self, *args) -> None:  # noqa
        if self.registered:
            self.logger.warning("Disconnected from server")
        else:
            self.logger.error("Failed to register engine")

    def on_register(self) -> None:
        self.registered = False
        self.logger.info("Received registration request from server")
        self.register()

    def on_registration_ack(self) -> None:
        self.logger.info(
            "Engine registration successful, sending initial ecosystems info")
        self.start_background_task()
        self.send_ecosystems_info()
        self.registered = True
        self.logger.info("Initial ecosystems info sent")

    def filter_uids(
            self,
            ecosystem_uids: str | list[str] | None = None
    ) -> list[str]:
        if ecosystem_uids is None:
            return [uid for uid in self.ecosystems.keys()]
        else:
            if isinstance(ecosystem_uids, str):
                ecosystem_uids = [ecosystem_uids]
            return [
                uid for uid in ecosystem_uids
                if uid in self.ecosystems.keys()
            ]

    def get_event_payload(
            self,
            event_name: EventNames,
            ecosystem_uids: str | list[str] | None = None
    ) -> list[EcosystemPayload]:
        rv = []
        uids = self.filter_uids(ecosystem_uids)
        self.logger.debug(
            f"Getting '{event_name}' payload for {humanize_list(uids)}")
        for uid in uids:
            if hasattr(self.ecosystems[uid], event_name):
                data = getattr(self.ecosystems[uid], event_name)
            else:
                self.logger.error(f"Payload for event {event_name} is not defined")
                return rv
            if not isinstance(data, Empty):
                payload_class = payload_classes[event_name]
                payload: EcosystemPayload = payload_class.from_base(uid, data)
                rv.append(payload)
        return rv

    def emit_event(
            self,
            event_name: EventNames,
            ecosystem_uids: str | list[str] | None = None
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
            ecosystem_uids: str | list[str] | None = None
    ) -> None:
        for cfg in ("base_info", "management", "environmental_parameters", "hardware"):
            cfg: EventNames
            self.emit_event(cfg, ecosystem_uids)

    def send_sensors_data(
            self,
            ecosystem_uids: str | list[str] | None = None
    ) -> None:
        self.emit_event("sensors_data", ecosystem_uids)

    def send_health_data(
            self,
            ecosystem_uids: str | list[str] | None = None
    ) -> None:
        self.emit_event("health_data", ecosystem_uids)

    def send_light_data(
            self,
            ecosystem_uids: str | list[str] | None = None
    ) -> None:
        self.emit_event("light_data", ecosystem_uids)

    def send_actuator_data(
            self,
            ecosystem_uids: str | list[str] | None = None
    ) -> None:
        self.emit_event("actuator_data", ecosystem_uids)

    def on_turn_light(self, message: dict) -> None:
        message["actuator"] = HardwareType.light
        self.on_turn_actuator(message)

    def on_turn_actuator(self, message: TurnActuatorPayloadDict) -> None:
        data: TurnActuatorPayloadDict = self.validate_payload(
            message, TurnActuatorPayload)
        ecosystem_uid: str = data["ecosystem_uid"]
        if ecosystem_uid in self.ecosystems:
            self.logger.debug("Received turn_actuator event")
            self.ecosystems[ecosystem_uid].turn_actuator(
                actuator=data["actuator"],
                mode=data["mode"],
                countdown=message.get("countdown", 0.0)
            )

    def on_change_management(self, message: ManagementConfigPayloadDict) -> None:
        data: ManagementConfigPayloadDict = self.validate_payload(
            message, ManagementConfigPayload)
        ecosystem_uid: str = data["uid"]
        if ecosystem_uid in self.ecosystems:
            for management, status in data["data"].items():
                self.ecosystems[ecosystem_uid].config.set_management(management, status)
            self.ecosystems[ecosystem_uid].config.save()
            self.emit_event("management", ecosystem_uids=[ecosystem_uid])

    def get_crud_function(
            self,
            crud_key: str,
            ecosystem_uid: str | None = None
    ) -> Callable:
        if (
                not "ecosystem" in crud_key
                and ecosystem_uid is None
        ):
            raise ValueError(f"{crud_key} requires 'ecosystem_uid' to be set")

        def assign(property_setter):
            def inner(payload: dict):
                property_setter = payload  # noqa

            return inner

        return {
            # Ecosystem creation/deletion
            "create_ecosystem": self.engine.config.create_ecosystem,
            "delete_ecosystem": self.engine.config.delete_ecosystem,
            # Ecosystem update
            "update_light_method": assign(self.ecosystems[ecosystem_uid].config.light_method),
            "update_chaos": assign(self.ecosystems[ecosystem_uid].config.chaos),
            "update_time_parameters": assign(self.ecosystems[ecosystem_uid].config.time_parameters),
            "create_environment_parameter": self.ecosystems[ecosystem_uid].config.set_climate_parameter,
            "update_environment_parameter": self.ecosystems[ecosystem_uid].config.set_climate_parameter,
            "delete_environment_parameter": self.ecosystems[ecosystem_uid].config.delete_climate_parameter,
            # Hardware creation, deletion and update
            "create_hardware": self.ecosystems[ecosystem_uid].config.create_new_hardware,
            "update_hardware": self.ecosystems[ecosystem_uid].config.update_hardware,
            "delete_hardware": self.ecosystems[ecosystem_uid].config.delete_hardware,
            # Private
            "update_place": self.engine.config.set_place,
        }[crud_key]

    def on_crud(self, message: CrudPayloadDict):
        data: CrudPayloadDict = self.validate_payload(
            message, CrudPayload)
        crud_uuid = data["uuid"]
        engine_uid = data["engine_uid"]
        if engine_uid != self.engine.uid:
            self.logger.error(
                f"Received 'on_crud' event intended to engine {engine_uid}"
            )
            return
        crud_key = f"{data['action'].value}_{data['target']}"
        ecosystem_uid = (
            data["values"].get("ecosystem_uid") or data["values"].get("uid")
        )
        crud_function = self.get_crud_function(crud_key, ecosystem_uid)
        try:
            crud_function(**data["values"])
            self.emit(
                event="crud_result",
                data=CrudResult(
                    uuid=crud_uuid,
                    status=Result.success
                ).dict()
            )
        except Exception as e:
            self.emit(
                event="crud_result",
                data=CrudResult(
                    uuid=crud_uuid,
                    status=Result.failure,
                    message=e
                ).dict()
            )

    def on_get_data_since(self, message: SynchronisationPayloadDict) -> None:
        if self.db is None:
            self.logger.error(
                "Received 'get_data_since' event but USE_DATABASE is set to False"
            )
            return
        message: SynchronisationPayloadDict = self.validate_payload(
            message, SynchronisationPayload)
        uids: list[str] = self.filter_uids(message["ecosystems"])
        since: datetime = message["since"]
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
