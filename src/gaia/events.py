from __future__ import annotations

from gaia.dependencies import check_dependencies

check_dependencies("dispatcher")

import inspect
import logging
from time import monotonic, sleep
import typing as t
from typing import Callable, cast, Literal, Type
import weakref

from pydantic import ValidationError

from dispatcher import EventHandler
import gaia_validators as gv

from gaia.config import EcosystemConfig
from gaia.config.from_files import ConfigType
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
        self._sensor_buffer_cls: "SensorBuffer" | None = None
        self._last_heartbeat: float = monotonic()
        self._jobs_scheduled: bool = False
        self.logger = logging.getLogger(f"gaia.engine.events_handler")

    @property
    def use_db(self) -> bool:
        return self.engine.use_db

    @property
    def db(self) -> "SQLAlchemyWrapper":
        return self.engine.db

    @property
    def sensor_buffer_cls(self) -> "SensorBuffer":
        if self._sensor_buffer_cls is None:
            if not self.use_db:
                raise AttributeError(
                    "'SensorBuffer' is not a valid attribute when the database is "
                    "not used")
            from gaia.database.models import SensorBuffer
            self._sensor_buffer_cls = SensorBuffer
        return self._sensor_buffer_cls

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
        return (
            self._dispatcher.connected
            and monotonic() - self._last_heartbeat < 30.0
        )

    def emit_event_if_connected(
            self,
            event_name: EventNames,
            ttl: int | None = None
    ) -> None:
        if not self.is_connected():
            self.logger.info(
                f"Events handler not currently connected. Scheduled emission "
                f"of event '{event_name}' aborted")
            return
        try:
            self.emit_event(event_name, ttl=ttl)
        except Exception as e:
            self.logger.error(
                f"Encountered an error while tying to emit event `{event_name}`. "
                f"ERROR msg: `{e.__class__.__name__} :{e}`")

    def _schedule_jobs(self) -> None:
        self.engine.scheduler.add_job(
            func=self.ping,
            id="events-ping",
            trigger="interval", seconds=15,
        )
        sensor_offset: str = str(int(self.engine.config.app_config.SENSORS_LOOP_PERIOD + 1))
        self.engine.scheduler.add_job(
            self.emit_event_if_connected, kwargs={"event_name": "sensors_data", "ttl": 15},
            id="events-send_sensors_data",
            trigger="cron", minute="*", second=sensor_offset,
            misfire_grace_time=10,
        )
        self.engine.scheduler.add_job(
            self.emit_event_if_connected, kwargs={"event_name": "light_data"},
            id="events-send_light_data",
            trigger="cron", hour="1",
            misfire_grace_time=10*60,
        )
        self.engine.scheduler.add_job(
            self.emit_event_if_connected, kwargs={"event_name": "health_data"},
            id="events-send_health_data",
            trigger="cron", hour="1",
            misfire_grace_time=10*60,
        )

    def _unschedule_jobs(self) -> None:
        self.engine.scheduler.remove_job(job_id="events-ping")
        self.engine.scheduler.remove_job(job_id="events-send_sensors_data")
        self.engine.scheduler.remove_job(job_id="events-send_light_data")
        self.engine.scheduler.remove_job(job_id="events-send_health_data")

    def ping(self) -> None:
        if self._dispatcher.connected:
            ecosystems = [ecosystem.uid for ecosystem in self.ecosystems.values()]
            self.logger.debug("Sending 'ping'.")
            self.emit("ping", data=ecosystems, ttl=20)

    def on_pong(self) -> None:
        self.logger.debug("Received 'pong'.")
        self._last_heartbeat = monotonic()

    def register(self) -> None:
        data = gv.EnginePayload(
            engine_uid=self.engine.config.app_config.ENGINE_UID,
            address=local_ip_address(),
        ).model_dump()
        self.emit("register_engine", data=data, ttl=15)

    def send_ecosystems_info(
            self,
            ecosystem_uids: str | list[str] | None = None
    ) -> None:
        uids = self.filter_uids(ecosystem_uids)
        self.emit_event("base_info", uids)
        self.emit_event("management", uids)
        self.emit_event("environmental_parameters", uids)
        self.emit_event("hardware", uids)
        self.emit_event("actuator_data", uids)
        self.emit_event("light_data", uids)

    def on_connect(self, environment) -> None:  # noqa
        self.logger.info("Connection to message broker successful.")
        if self.registered:
            self.logger.info("Already registered.")
        else:
            self.logger.info("Will try to register the engine to Ouranos.")
            self.register()

    def on_disconnect(self, *args) -> None:  # noqa
        if self.engine.stopping:
            self.logger.info("Engine requested to disconnect from the broker.")
        elif self.registered:
            self.logger.warning("Dispatcher disconnected from the broker.")
        else:
            self.logger.error("Failed to register engine.")
        if self._jobs_scheduled:
            self._unschedule_jobs()
            self._jobs_scheduled = False

    def on_register(self) -> None:
        self.registered = False
        self.logger.info("Received registration request from Ouranos.")
        sleep(0.25)
        self.register()

    def on_registration_ack(self, host_uid: str) -> None:
        if self._dispatcher.host_uid != host_uid:
            self.logger.warning(
                "Received a registration acknowledgment for another dispatcher.")
            return
        self.logger.info(
            "Engine registration successful, sending initial ecosystems info.")
        self.send_ecosystems_info()
        self.logger.info("Initial ecosystems info sent.")
        if self.use_db:
            self.send_buffered_data()
        self.registered = True
        sleep(0.75)
        if not self._jobs_scheduled:
            self._schedule_jobs()
            self._jobs_scheduled = True
        self.emit("initialized", ttl=15)

    def on_initialized_ack(self, missing_data: list | None = None) -> None:
        if missing_data is None:
            self.logger.info("Ouranos successfully received ecosystems info.")
        else:
            self.logger.warning(
                f"Ouranos did not receive all the initial ecosystems info. "
                f"Non-received info: {missing_data}")
            # TODO: resend ?

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
            ecosystem_uids: str | list[str] | None = None,
            ttl: int | None = None,
    ) -> bool:
        self.logger.debug(f"Sending event {event_name} requested")
        payload = self.get_event_payload(event_name, ecosystem_uids)
        if payload:
            self.logger.debug(f"Payload for event {event_name} sent")
            return self.emit(event_name, data=payload, ttl=ttl)
        else:
            self.logger.debug(f"No payload for event {event_name}")
            return False

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
