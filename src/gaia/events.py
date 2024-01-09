from __future__ import annotations

from gaia.dependencies import check_dependencies

check_dependencies("dispatcher")

import inspect
import logging
from threading import Event, Thread
from time import sleep
import typing as t
from typing import Callable, cast, Literal, Type
import weakref

from pydantic import ValidationError

from dispatcher import EventHandler
import gaia_validators as gv

from gaia.config import EcosystemConfig
from gaia.config.from_files import ConfigType
from gaia.shared_resources import get_scheduler
from gaia.utils import humanize_list, local_ip_address


if t.TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy_wrapper import SQLAlchemyWrapper

    from gaia.database.models import SensorBuffer
    from gaia.ecosystem import Ecosystem
    from gaia.engine import Engine


EventNames = Literal[
    "base_info", "management", "environmental_parameters", "hardware",
    "sensors_data", "health_data", "light_data", "actuator_data"]


payload_classes: dict[EventNames, Type[gv.EcosystemPayload]] = {
    "base_info": gv.BaseInfoConfigPayload,
    "management": gv.ManagementConfigPayload,
    "environmental_parameters": gv.EnvironmentConfigPayload,
    "hardware": gv.HardwareConfigPayload,
    "sensors_data": gv.SensorsDataPayload,
    "health_data": gv.HealthDataPayload,
    "light_data": gv.LightDataPayload,
    "actuator_data": gv.ActuatorsDataPayload,
}


class Events(EventHandler):
    """A class holding all the events coming from event-dispatcher

    :param engine: an `Engine` instance
    """
    def __init__(self, engine: "Engine", **kwargs) -> None:
        kwargs["namespace"] = "aggregator"
        super().__init__(**kwargs)
        self.engine: "Engine" = weakref.proxy(engine)
        self.ecosystems: dict[str, "Ecosystem"] = self.engine.ecosystems
        self.registered = False
        self._thread: Thread | None = None
        self._stop_event = Event()
        self._sensor_buffer_cls: "SensorBuffer" | None = None
        if self.use_db:
            self.load_sensor_buffer_cls()
        self.logger = logging.getLogger(f"gaia.engine.events_handler")

    @property
    def use_db(self) -> bool:
        return self.engine.use_db

    @property
    def db(self) -> "SQLAlchemyWrapper":
        return self.engine.db

    @property
    def thread(self) -> Thread:
        if self._thread is None:
            raise AttributeError("Events thread has not been set up")
        return self._thread

    @thread.setter
    def thread(self, thread: Thread | None) -> None:
        self._thread = thread

    @property
    def background_tasks_running(self) -> bool:
        return self._thread is not None

    @property
    def sensor_buffer_cls(self) -> "SensorBuffer":
        if self._sensor_buffer_cls is None:
            raise AttributeError(
                "'SensorBuffer' is not a valid attribute when the database is "
                "not used")
        return self._sensor_buffer_cls

    def load_sensor_buffer_cls(self) -> None:
        if self.use_db:
            from gaia.database.models import SensorBuffer
            self._sensor_buffer_cls = SensorBuffer
        else:
            raise ValueError(
                "Cannot load 'SensorBuffer' when the database is not used")

    def validate_payload(
            self,
            data: dict,
            model_cls: Type[gv.BaseModel],
    ) -> dict:
        if not data:
            event = inspect.stack()[1].function.lstrip("on_")
            self.logger.error(
                f"Encountered an error while validating '{event}' data. Error "
                f"msg: Empty data."
            )
            raise ValidationError
        try:
            return model_cls(**data).model_dump()
        except ValidationError as e:
            event = inspect.stack()[1].function.lstrip("on_")
            msg_list = [f"{error['loc'][0]}: {error['msg']}" for error in e.errors()]
            self.logger.error(
                f"Encountered an error while validating '{event}' data. Error "
                f"msg: {', '.join(msg_list)}"
            )
            raise

    def is_connected(self) -> bool:
        if self._dispatcher.connected:
            return True
        return False

    def emit_event_if_connected(self, event_name: EventNames) -> None:
        if not self.is_connected():
            self.logger.info(
                f"Events handler not currently connected. Scheduled emission "
                f"of event '{event_name}' aborted")
            return
        try:
            self.emit_event(event_name)
        except Exception as e:
            self.logger.error(
                f"Encountered an error while tying to emit event `{event_name}`. "
                f"ERROR msg: `{e.__class__.__name__} :{e}`")

    def _schedule_jobs(self) -> None:
        scheduler = get_scheduler()
        scheduler.add_job(
            self.emit_event_if_connected, kwargs={"event_name": "sensors_data"},
            id="send_sensors_data", trigger="cron", minute="*",
            misfire_grace_time=10
        )
        scheduler.add_job(
            self.emit_event_if_connected, kwargs={"event_name": "light_data"},
            id="send_light_data", trigger="cron", hour="1",
            misfire_grace_time=10*60
        )
        scheduler.add_job(
            self.emit_event_if_connected, kwargs={"event_name": "health_data"},
            id="send_health_data", trigger="cron", hour="1",
            misfire_grace_time=10*60
        )

    def _unschedule_jobs(self) -> None:
        scheduler = get_scheduler()
        scheduler.remove_job(job_id="send_sensors_data")
        scheduler.remove_job(job_id="send_light_data")
        scheduler.remove_job(job_id="send_health_data")

    def start_background_tasks(self) -> None:
        self._schedule_jobs()
        self._stop_event.clear()
        self.thread = Thread(
            target=self.ping_loop,
            name="events_ping",
            daemon=True,
        )
        self.thread.start()

    def stop_background_tasks(self) -> None:
        self._unschedule_jobs()
        self._stop_event.set()
        self.thread.join()
        self.thread = None

    def ping_loop(self) -> None:
        sleep(0.1)  # Sleep to allow the end of dispatcher initialization if it directly connects
        while not self._stop_event.is_set():
            if self.is_connected():
                self.ping()
            sleep(15)

    def ping(self) -> None:
        ecosystems = [ecosystem.uid for ecosystem in self.ecosystems.values()]
        self.emit("ping", data=ecosystems, ttl=20)

    def register(self) -> None:
        data = gv.EnginePayload(
            engine_uid=self.engine.config.app_config.ENGINE_UID,
            address=local_ip_address(),
        ).model_dump()
        self.emit("register_engine", data=data, ttl=2)

    def send_ecosystems_info(
            self,
            ecosystem_uids: str | list[str] | None = None
    ) -> None:
        uids = self.filter_uids(ecosystem_uids)
        self.send_full_config(uids)
        # self.send_sensors_data(uids)
        self.send_actuator_data(uids)
        self.send_light_data(uids)
        self.send_health_data(uids)

    def on_connect(self, environment) -> None:  # noqa
        self.logger.info("Connection to message broker successful")
        if not self.registered:
            self.register()

    def on_disconnect(self, *args) -> None:  # noqa
        if self.engine.stopping:
            self.logger.info("Engine requested to disconnect from the broker.")
        elif self.registered:
            self.logger.warning("Dispatcher disconnected from the broker")
        else:
            self.logger.error("Failed to register engine")
        if self.background_tasks_running:
            self.stop_background_tasks()

    def on_register(self) -> None:
        self.registered = False
        self.logger.info("Received registration request from Ouranos")
        sleep(1)
        self.register()
        if not self.background_tasks_running:
            self.start_background_tasks()

    def on_registration_ack(self) -> None:
        self.logger.info(
            "Engine registration successful, sending initial ecosystems info")
        self.send_ecosystems_info()
        self.logger.info("Initial ecosystems info sent")
        if self.use_db:
            self.send_buffered_data()
        self.registered = True


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
    ) -> list[gv.EcosystemPayloadDict]:
        rv: list[gv.EcosystemPayloadDict] = []
        uids = self.filter_uids(ecosystem_uids)
        self.logger.debug(
            f"Getting '{event_name}' payload for {humanize_list(uids)}")
        for uid in uids:
            if hasattr(self.ecosystems[uid], event_name):
                data = getattr(self.ecosystems[uid], event_name)
            else:
                self.logger.error(f"Payload for event {event_name} is not defined")
                return rv
            if not isinstance(data, gv.Empty):
                payload_class = payload_classes[event_name]
                payload: gv.EcosystemPayload = payload_class.from_base(uid, data)
                payload_dict: gv.EcosystemPayloadDict = payload.model_dump()
                rv.append(payload_dict)
        return rv

    def emit_event(
            self,
            event_name: EventNames,
            ecosystem_uids: str | list[str] | None = None
    ) -> bool:
        self.logger.debug(f"Sending event {event_name} requested")
        payload = self.get_event_payload(event_name, ecosystem_uids)
        if payload:
            self.logger.debug(f"Payload for event {event_name} sent")
            return self.emit(event_name, data=payload)
        else:
            self.logger.debug(f"No payload for event {event_name}")
            return False

    def send_full_config(
            self,
            ecosystem_uids: str | list[str] | None = None
    ) -> None:
        for cfg in ("base_info", "management", "environmental_parameters", "hardware"):
            cfg = cast(EventNames, cfg)
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

    def on_turn_light(self, message: gv.TurnActuatorPayloadDict) -> None:
        message["actuator"] = gv.HardwareType.light
        self.on_turn_actuator(message)

    def on_turn_actuator(self, message: gv.TurnActuatorPayloadDict) -> None:
        data: gv.TurnActuatorPayloadDict = self.validate_payload(
            message, gv.TurnActuatorPayload)
        ecosystem_uid: str = data["ecosystem_uid"]
        if ecosystem_uid in self.ecosystems:
            self.logger.debug("Received turn_actuator event")
            self.ecosystems[ecosystem_uid].turn_actuator(
                actuator=data["actuator"],
                mode=data["mode"],
                countdown=message.get("countdown", 0.0)
            )

    def on_change_management(self, message: gv.ManagementConfigPayloadDict) -> None:
        data: gv.ManagementConfigPayloadDict = self.validate_payload(
            message, gv.ManagementConfigPayload)
        ecosystem_uid: str = data["uid"]
        if ecosystem_uid in self.ecosystems:
            for management, status in data["data"].items():
                self.ecosystems[ecosystem_uid].config.set_management(management, status)
            self.engine.config.save(ConfigType.ecosystems)
            self.emit_event("management", ecosystem_uids=[ecosystem_uid])

    def get_CRUD_function(
            self,
            crud_key: str,
            ecosystem_uid: str | None = None
    ) -> Callable:
        if "ecosystem" in crud_key:
            return {
            # Ecosystem creation and deletion
            "create_ecosystem": self.engine.config.create_ecosystem,
            "delete_ecosystem": self.engine.config.delete_ecosystem,
            }[crud_key]
        else:
            if ecosystem_uid is None:
                raise ValueError(f"{crud_key} requires 'ecosystem_uid' to be set")
        ecosystem_uid = cast(str, ecosystem_uid)

        def CRUD_update(config: EcosystemConfig, attr_name: str) -> Callable:
            def inner(payload: dict):
                setattr(config, attr_name, payload)

            return inner

        return {
            # Ecosystem properties update
            "update_chaos": CRUD_update(self.ecosystems[ecosystem_uid].config, "chaos"),
            "update_light_method": CRUD_update(self.ecosystems[ecosystem_uid].config, "light_method"),
            "update_management": CRUD_update(self.ecosystems[ecosystem_uid].config, "managements"),
            "update_time_parameters": CRUD_update(self.ecosystems[ecosystem_uid].config, "time_parameters"),
            # Environment parameter creation, deletion and update
            "create_environment_parameter": self.ecosystems[ecosystem_uid].config.CRUD_create_climate_parameter,
            "update_environment_parameter": self.ecosystems[ecosystem_uid].config.CRUD_update_climate_parameter,
            "delete_environment_parameter": self.ecosystems[ecosystem_uid].config.delete_climate_parameter,
            # Hardware creation, deletion and update
            "create_hardware": self.ecosystems[ecosystem_uid].config.CRUD_create_hardware,
            "update_hardware": self.ecosystems[ecosystem_uid].config.CRUD_update_hardware,
            "delete_hardware": self.ecosystems[ecosystem_uid].config.delete_hardware,
            # Private
            "create_place": self.engine.config.CRUD_create_place,
            "update_place": self.engine.config.CRUD_update_place,
        }[crud_key]

    def get_CRUD_event_name(self, crud_key: str) -> EventNames:
        # TODO: handle ecosystem creation and deletion
        return {
            # Ecosystem creation and deletion
            "create_ecosystem": "base_info",
            "delete_ecosystem": "base_info",
            # Ecosystem properties update
            #"update_chaos": ,
            "update_light_method": "light_data",
            "update_management": "management",
            "update_time_parameters": "light_data",
            # Environment parameter creation, deletion and update
            "create_environment_parameter": "environmental_parameters",
            "update_environment_parameter": "environmental_parameters",
            "delete_environment_parameter": "environmental_parameters",
            # Hardware creation, deletion and update
            "create_hardware": "hardware",
            "update_hardware": "hardware",
            "delete_hardware": "hardware",
            # Private
            #"create_place": ,
            #"update_place": ,
        }[crud_key]

    def on_crud(self, message: gv.CrudPayloadDict) -> None:
        data: gv.CrudPayloadDict = self.validate_payload(
            message, gv.CrudPayload)
        crud_uuid = data["uuid"]
        self.logger.info(
            f"Received CRUD request '{crud_uuid}' from Ouranos")
        engine_uid = data["routing"]["engine_uid"]
        if engine_uid != self.engine.uid:
            self.logger.warning(
                f"Received 'on_crud' event intended to engine {engine_uid}"
            )
            return
        crud_key = f"{data['action'].value}_{data['target']}"
        ecosystem_uid = data["routing"]["ecosystem_uid"]
        try:
            crud_function = self.get_CRUD_function(crud_key, ecosystem_uid)
        except KeyError:
            self.logger.error(
                f"No CRUD function linked to action '{data['action'].value}' on"
                f"target '{data['target']}' could be found. Aborting")
            return
        try:
            crud_function(data["data"])
            self.engine.config.save(ConfigType.ecosystems)
            self.emit(
                event="crud_result",
                data=gv.RequestResult(
                    uuid=crud_uuid,
                    status=gv.Result.success
                ).model_dump()
            )
            self.logger.info(
                f"CRUD request '{crud_uuid}' was successfully treated")
            try:
                event_name = self.get_CRUD_event_name(crud_key)
            except KeyError:
                self.logger.debug(
                    f"No CRUD payload linked to action '{data['action'].value}' "
                    f"on target '{data['target']}' was found. New data won't be "
                    f"sent to Ouranos")
            else:
                self.emit_event(
                    event_name=event_name, ecosystem_uids=ecosystem_uid)
        except Exception as e:
            self.emit(
                event="crud_result",
                data=gv.RequestResult(
                    uuid=crud_uuid,
                    status=gv.Result.failure,
                    message=str(e)
                ).model_dump()
            )
            self.logger.info(
                f"CRUD request '{crud_uuid}' could not be treated")

    def send_buffered_data(self) -> None:
        if not self.use_db:
            raise RuntimeError(
                "The database is not enabled. To enable it, set configuration "
                "parameter 'USE_DATABASE' to 'True'")
        SensorBuffer = self.sensor_buffer_cls  # noqa
        with self.db.scoped_session() as session:
            for data in SensorBuffer.get_buffered_data(session):
                self.emit(
                    event="buffered_sensors_data", data=data)

    def on_buffered_data_ack(self, message: gv.RequestResultDict) -> None:
        if not self.use_db:
            raise RuntimeError(
                "The database is not enabled. To enable it, set configuration "
                "parameter 'USE_DATABASE' to 'True'")
        data: gv.RequestResultDict = self.validate_payload(
            message, gv.RequestResult)
        SensorBuffer = self.sensor_buffer_cls  # noqa
        with self.db.scoped_session() as session:
            if data["status"] == gv.Result.success:
                SensorBuffer.clear_buffer(session, data["uuid"])
            else:
                SensorBuffer.clear_uuid(session, data["uuid"])
