from __future__ import annotations

from gaia.dependencies import check_dependencies

check_dependencies("dispatcher")

import inspect
import logging
from time import monotonic, sleep
import typing as t
from typing import Any, Callable, Literal, NamedTuple, Type
from uuid import UUID
import weakref

from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
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
    "sensors_data", "health_data", "light_data", "actuator_data", "chaos_parameters"]


payload_classes: dict[EventNames, Type[gv.EcosystemPayload]] = {
    "base_info": gv.BaseInfoConfigPayload,
    "management": gv.ManagementConfigPayload,
    "environmental_parameters": gv.EnvironmentConfigPayload,
    "hardware": gv.HardwareConfigPayload,
    "sensors_data": gv.SensorsDataPayload,
    "health_data": gv.HealthDataPayload,
    "light_data": gv.LightDataPayload,
    "actuator_data": gv.ActuatorsDataPayload,
    "chaos": gv.ChaosParametersPayload,
}


class CrudLinks(NamedTuple):
    function_name: str
    event_name: EventNames


crud_links: dict[str, CrudLinks] = {
    # Ecosystem creation and deletion
    "create_ecosystem": CrudLinks("create_ecosystem", "base_info"),
    "delete_ecosystem": CrudLinks("delete_ecosystem", "base_info"),
    # Places creation, update and deletion
    "create_place": CrudLinks("set_place", ""),
    "update_place": CrudLinks("update_place", ""),
    "delete_place": CrudLinks("delete_place", ""),
    # Ecosystem properties update
    "update_chaos": CrudLinks("chaos", "chaos_parameters"),
    "update_management": CrudLinks("managements", "management"),
    "update_time_parameters": CrudLinks("time_parameters", "light_data"),
    "update_light_method": CrudLinks("set_light_method", "light_data"),
    # Environment parameter creation, deletion and update
    "create_environment_parameter": CrudLinks("set_climate_parameter", "environmental_parameters"),
    "update_environment_parameter": CrudLinks("update_climate_parameter", "environmental_parameters"),
    "delete_environment_parameter": CrudLinks("delete_climate_parameter", "environmental_parameters"),
    # Hardware creation, deletion and update
    "create_hardware": CrudLinks("create_new_hardware", "hardware"),
    "update_hardware": CrudLinks("update_hardware", "hardware"),
    "delete_hardware": CrudLinks("delete_hardware", "hardware"),
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
            ecosystem_uids: str | list[str] | None = None,
            ttl: int | None = None
    ) -> None:
        if not self.is_connected():
            self.logger.info(
                f"Events handler not currently connected. Emission of event "
                f"'{event_name}' aborted.")
            return
        self.emit_event(event_name, ecosystem_uids=ecosystem_uids, ttl=ttl)

    def _schedule_jobs(self) -> None:
        self.engine.scheduler.add_job(
            func=self.ping,
            id="events-ping",
            trigger=IntervalTrigger(seconds=15),
        )
        sensor_offset: str = str(int(self.engine.config.app_config.SENSORS_LOOP_PERIOD + 1))
        self.engine.scheduler.add_job(
            func=self.emit_event_if_connected, kwargs={"event_name": "sensors_data", "ttl": 15},
            id="events-send_sensors_data",
            trigger=CronTrigger(minute="*", second=sensor_offset),
            misfire_grace_time=10,
        )
        self.engine.scheduler.add_job(
            func=self.emit_event_if_connected, kwargs={"event_name": "light_data"},
            id="events-send_light_data",
            trigger=CronTrigger(hour="1", jitter=5.0),
            misfire_grace_time=10 * 60,
        )
        self.engine.scheduler.add_job(
            func=self.emit_event_if_connected, kwargs={"event_name": "health_data"},
            id="events-send_health_data",
            trigger=CronTrigger(hour="1", jitter=5.0),
            misfire_grace_time=10 * 60,
        )
        self._jobs_scheduled = True

    def _unschedule_jobs(self) -> None:
        self.engine.scheduler.remove_job(job_id="events-ping")
        self.engine.scheduler.remove_job(job_id="events-send_sensors_data")
        self.engine.scheduler.remove_job(job_id="events-send_light_data")
        self.engine.scheduler.remove_job(job_id="events-send_health_data")
        self._jobs_scheduled = False

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
            return # The Engine takes care to shut down the scheduler and the jobs running
        elif self.registered:
            self.logger.warning("Dispatcher disconnected from the broker.")
        else:
            self.logger.error("Failed to register engine.")
        if self._jobs_scheduled:
            self._unschedule_jobs()

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
            f"Getting '{event_name}' payload for {humanize_list(uids)}.")
        for uid in uids:
            if hasattr(self.ecosystems[uid], event_name):
                data = getattr(self.ecosystems[uid], event_name)
            else:
                self.logger.error(f"Payload for event '{event_name}' is not defined.")
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
        self.logger.debug(f"Requested to emit event '{event_name}'.")
        payload = self.get_event_payload(event_name, ecosystem_uids)
        if payload:
            try:
                result = self.emit(event_name, data=payload, ttl=ttl)
            except Exception as e:
                self.logger.error(
                    f"Encountered an error while emitting event '{event_name}'. "
                    f"ERROR msg: `{e.__class__.__name__}: {e}`.")
            else:
                self.logger.debug(f"Payload for event '{event_name}' sent.")
                return result
        else:
            self.logger.debug(f"No payload for event '{event_name}' found.")
            return False

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

    # TODO: use CRUD
    def on_change_management(self, message: gv.ManagementConfigPayloadDict) -> None:
        data: gv.ManagementConfigPayloadDict = self.validate_payload(
            message, gv.ManagementConfigPayload)
        ecosystem_uid: str = data["uid"]
        if ecosystem_uid in self.ecosystems:
            for management, status in data["data"].items():
                self.ecosystems[ecosystem_uid].config.set_management(management, status)
            self.engine.config.save(ConfigType.ecosystems)
            self.emit_event("management", ecosystem_uids=[ecosystem_uid])

    def _get_crud_function(
            self,
            action: gv.CrudAction,
            target: str,
            ecosystem_uid: str | None = None
    ) -> Callable:
        if target in ("ecosystem", "place"):
            base_obj = self.engine.config
        else:
            if ecosystem_uid is None:
                raise ValueError(
                    f"{action.name.capitalize()} {target} requires the "
                    f"'ecosystem_uid' field to be set.")
            if ecosystem_uid not in self.engine.ecosystems:
                pass
                raise ValueError(
                    f"Ecosystem with uid '{ecosystem_uid}' is not one of the "
                    f"started ecosystems.")
            base_obj = self.engine.ecosystems[ecosystem_uid].config

        crud_key = f"{action.name}_{target}"

        crud_link = crud_links.get(crud_key)
        if crud_link is None:
            raise ValueError(
                f"{action.name.capitalize()} {target} is not possible for this "
                f"engine.")

        if target in ("management", "time_parameters"):
            # Need to update a setter
            def crud_update_setter(config: EcosystemConfig, attr_name: str) -> Callable:
                def inner(**value: dict):
                    setattr(config, attr_name, value)

                return inner

            return crud_update_setter(base_obj, crud_link.function_name)
        else:
            def get_function(obj: Any, func_name: str) -> Callable:
                return getattr(obj, func_name)

            return get_function(base_obj, crud_link.function_name)

    def on_crud(self, message: gv.CrudPayloadDict) -> None:
        # Validate the payload
        data: gv.CrudPayloadDict = self.validate_payload(message, gv.CrudPayload)
        # Verify it is the intended recipient
        engine_uid = data["routing"]["engine_uid"]
        if engine_uid != self.engine.uid:
            self.logger.warning(
                f"Received a CRUD request intended to engine '{engine_uid}'.")
            return

        # Extract CRUD request data
        crud_uuid: UUID = data["uuid"]
        action: gv.CrudAction = data['action']
        target: str = data['target']
        ecosystem_uid: str | None = data["routing"]["ecosystem_uid"]
        crud_key = f"{action.name}_{target}"
        self.logger.info(f"Received CRUD request '{crud_uuid}' from Ouranos.")

        # Treat the CRUD request
        try:
            crud_function = self._get_crud_function(action, target, ecosystem_uid)
            crud_function(**data["data"])
            self.engine.config.save(ConfigType.ecosystems)
        except Exception as e:
            self.logger.error(
                f"Encountered an error while treating CRUD request "
                f"`{crud_uuid}`. ERROR msg: `{e.__class__.__name__}: {e}`.")
            self.emit(
                event="crud_result",
                data=gv.RequestResult(
                    uuid=crud_uuid,
                    status=gv.Result.failure,
                    message=str(e)
                ).model_dump()
            )
            return
        else:
            self.emit(
                event="crud_result",
                data=gv.RequestResult(
                    uuid=crud_uuid,
                    status=gv.Result.success
                ).model_dump()
            )
            self.logger.info(
                f"CRUD request '{crud_uuid}' was successfully treated.")

        # Send back the updated info
        self.engine.refresh_ecosystems(send_info=False)
        crud_link = crud_links[crud_key]
        if not crud_link.event_name:
            self.logger.warning(
                f"No CRUD payload linked to action '{action.name} {target}' "
                f"was found. Updated data won't be sent to Ouranos.")
        event_name = crud_link.event_name
        self.emit_event(event_name=event_name, ecosystem_uids=ecosystem_uid)

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
