from __future__ import annotations

from gaia.dependencies import check_dependencies

check_dependencies("dispatcher")

import inspect
import logging
from time import monotonic, sleep
import typing as t
from typing import Any, Callable, cast, Literal, NamedTuple, Type
from uuid import UUID
import weakref

from apscheduler.triggers.interval import IntervalTrigger
from pydantic import ValidationError

from dispatcher import AsyncEventHandler
import gaia_validators as gv

from gaia import Ecosystem, EcosystemConfig, Engine
from gaia.config.from_files import ConfigType
from gaia.utils import humanize_list, local_ip_address


if t.TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy_wrapper import SQLAlchemyWrapper

    from gaia.database.models import SensorBuffer


PayloadName = Literal[
    "base_info", "management", "environmental_parameters", "hardware",
    "sensors_data", "health_data", "light_data", "actuator_data",
    "chaos_parameters", "places_list",
]


payload_classes_dict: dict[PayloadName, Type[gv.EcosystemPayload]] = {
    "base_info": gv.BaseInfoConfigPayload,
    "management": gv.ManagementConfigPayload,
    "environmental_parameters": gv.EnvironmentConfigPayload,
    "hardware": gv.HardwareConfigPayload,
    "sensors_data": gv.SensorsDataPayload,
    "health_data": gv.HealthDataPayload,
    "light_data": gv.LightDataPayload,
    "actuator_data": gv.ActuatorsDataPayload,
    "chaos_parameters": gv.ChaosParametersPayload,
    "places_list": gv.PlacesPayload,
}


class CrudLinks(NamedTuple):
    func_or_attr_name: str
    payload_name: PayloadName


CrudEventName = Literal[
    "create_ecosystem", "update_ecosystem", "delete_ecosystem",
    "create_place", "update_place", "delete_place",
    "update_chaos_config", "update_management", "update_time_parameters",
    "update_light_method",
    "create_environment_parameter", "update_environment_parameter",
    "delete_environment_parameter",
    "create_hardware", "update_hardware", "delete_hardware",
]


crud_links_dict: dict[CrudEventName, CrudLinks] = {
    # Ecosystem creation and deletion
    "create_ecosystem": CrudLinks("create_ecosystem", "base_info"),
    "update_ecosystem": CrudLinks("update_ecosystem", "base_info"),
    "delete_ecosystem": CrudLinks("delete_ecosystem", "base_info"),
    # Places creation, update and deletion
    "create_place": CrudLinks("set_place", "places_list"),
    "update_place": CrudLinks("update_place", "places_list"),
    "delete_place": CrudLinks("delete_place", "places_list"),
    # Ecosystem properties update
    "update_chaos_config": CrudLinks("chaos_config", "chaos_parameters"),
    "update_management": CrudLinks("managements", "management"),
    "update_time_parameters": CrudLinks("time_parameters", "light_data"),
    "update_light_method": CrudLinks("set_lighting_method", "light_data"),
    # Environment parameter creation, deletion and update
    "create_environment_parameter": CrudLinks(
        "set_climate_parameter", "environmental_parameters"),
    "update_environment_parameter": CrudLinks(
        "update_climate_parameter", "environmental_parameters"),
    "delete_environment_parameter": CrudLinks(
        "delete_climate_parameter", "environmental_parameters"),
    # Hardware creation, deletion and update
    "create_hardware": CrudLinks("create_new_hardware", "hardware"),
    "update_hardware": CrudLinks("update_hardware", "hardware"),
    "delete_hardware": CrudLinks("delete_hardware", "hardware"),
}


class Events(AsyncEventHandler):
    """A class holding all the events coming from event-dispatcher

    :param engine: an `Engine` instance
    """
    def __init__(self, engine: Engine, **kwargs) -> None:
        kwargs["namespace"] = "aggregator"
        super().__init__(**kwargs)
        self.engine: Engine = weakref.proxy(engine)
        self.ecosystems: dict[str, "Ecosystem"] = self.engine.ecosystems
        self.registered = False
        self._sensor_buffer_cls: SensorBuffer | None = None
        self._last_heartbeat: float = monotonic()
        self._jobs_scheduled: bool = False
        self.logger = logging.getLogger(f"gaia.engine.events_handler")

    @property
    def use_db(self) -> bool:
        return self.engine.use_db

    @property
    def db(self) -> SQLAlchemyWrapper:
        return self.engine.db

    @property
    def sensor_buffer_cls(self) -> SensorBuffer:
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

    async def send_payload_if_connected(
            self,
            payload_name: PayloadName,
            ecosystem_uids: str | list[str] | None = None,
            ttl: int | None = None
    ) -> None:
        if not self.is_connected():
            self.logger.debug(
                f"Events handler not currently connected. Emission of event "
                f"'{payload_name}' aborted.")
            return
        await self.send_payload(payload_name, ecosystem_uids=ecosystem_uids, ttl=ttl)

    def _schedule_jobs(self) -> None:
        self.engine.scheduler.add_job(
            func=self.ping,
            id="events-ping",
            trigger=IntervalTrigger(seconds=15),
        )
        self._jobs_scheduled = True

    def _unschedule_jobs(self) -> None:
        self.engine.scheduler.remove_job(job_id="events-ping")
        self._jobs_scheduled = False

    async def ping(self) -> None:
        if self._dispatcher.connected:
            ecosystems = [{
                "uid": ecosystem.uid,
                "status": ecosystem.started,
            } for ecosystem in self.ecosystems.values()]
            self.logger.debug("Sending 'ping'.")
            await self.emit("ping", data=ecosystems, ttl=20)

    async def on_pong(self) -> None:
        self.logger.debug("Received 'pong'.")
        self._last_heartbeat = monotonic()

    async def register(self) -> None:
        data = gv.EnginePayload(
            engine_uid=self.engine.config.app_config.ENGINE_UID,
            address=local_ip_address(),
        ).model_dump()
        result = await self.emit("register_engine", data=data, ttl=15)
        if result:
            self.logger.debug("Registration request sent.")
        else:
            self.logger.warning("Registration request could not be sent.")

    async def send_ecosystems_info(
            self,
            ecosystem_uids: str | list[str] | None = None
    ) -> None:
        await self.send_payload("places_list")
        uids = self.filter_uids(ecosystem_uids)
        await self.send_payload("base_info", uids)
        await self.send_payload("management", uids)
        await self.send_payload("environmental_parameters", uids)
        await self.send_payload("hardware", uids)
        await self.send_payload("actuator_data", uids)
        await self.send_payload("light_data", uids)

    async def on_connect(self, environment) -> None:  # noqa
        self.logger.info("Connection to message broker successful.")
        self.logger.info("Will try to register the engine to Ouranos.")
        await self.register()

    async def on_disconnect(self, *args) -> None:  # noqa
        if self.engine.stopping:
            self.logger.info("Engine requested to disconnect from the broker.")
            return  # The Engine takes care to shut down the scheduler and the jobs running
        elif self.registered:
            self.logger.warning("Dispatcher disconnected from the broker.")
        else:
            self.logger.error("Failed to register engine.")
        if self._jobs_scheduled:
            self._unschedule_jobs()

    async def on_register(self) -> None:
        self.registered = False
        self.logger.info("Received registration request from Ouranos.")
        sleep(0.25)
        await self.register()

    async def on_registration_ack(self, host_uid: str) -> None:
        if self._dispatcher.host_uid != UUID(host_uid):
            self.logger.warning(
                "Received a registration acknowledgment for another dispatcher.")
            return
        self.logger.info(
            "Engine registration successful, sending initial ecosystems info.")
        await self.send_ecosystems_info()
        self.logger.info("Initial ecosystems info sent.")
        if self.use_db:
            await self.send_buffered_data()
        self.registered = True
        sleep(0.75)
        if not self._jobs_scheduled:
            self._schedule_jobs()
        await self.emit("initialized", ttl=15)

    async def on_initialized_ack(self, missing_data: list | None = None) -> None:
        if missing_data is None:
            self.logger.info("Ouranos successfully received ecosystems info.")
        else:
            self.logger.warning(
                f"Ouranos did not receive all the initial ecosystems info. "
                f"Non-received info: {humanize_list(missing_data)}.")
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

    def get_payload(
            self,
            payload_name: PayloadName,
            ecosystem_uids: str | list[str] | None = None
    ) -> gv.EcosystemPayloadDict | list[gv.EcosystemPayloadDict] | None:
        self.logger.debug(f"Getting '{payload_name}' payload.")
        if payload_name in ("places_list",):
            return self._get_engine_payload(payload_name)
        else:
            return self._get_ecosystem_payload(payload_name, ecosystem_uids)

    def _get_ecosystem_payload(
            self,
            payload_name: PayloadName,
            ecosystem_uids: str | list[str] | None = None
    ) -> list[gv.EcosystemPayloadDict] | None:
        # Check that the event is possible
        if not hasattr(Ecosystem, payload_name):
            self.logger.error(f"Payload for event '{payload_name}' is not defined.")
            return None
        # Get the data
        rv: list[gv.EcosystemPayloadDict] = []
        uids = self.filter_uids(ecosystem_uids)
        self.logger.debug(
            f"Getting '{payload_name}' payload for {humanize_list(uids)}.")
        for uid in uids:
            data = getattr(self.ecosystems[uid], payload_name)
            if isinstance(data, gv.Empty):
                continue
            payload_class = payload_classes_dict[payload_name]
            payload: gv.EcosystemPayload = payload_class.from_base(uid, data)
            payload_dict: gv.EcosystemPayloadDict = payload.model_dump()
            rv.append(payload_dict)
        return rv

    def _get_engine_payload(
            self,
            payload_name: PayloadName
    ) -> gv.EcosystemPayloadDict | None:
        # Check that the event is possible
        if not hasattr(Engine, payload_name):
            self.logger.error(f"Payload for event '{payload_name}' is not defined.")
            return None
        # Get the data
        data = getattr(self.engine, payload_name)
        payload_class = payload_classes_dict[payload_name]
        payload: gv.EcosystemPayload = payload_class.from_base(self.engine.uid, data)
        payload_dict: gv.EcosystemPayloadDict = payload.model_dump()
        return payload_dict

    async def send_payload(
            self,
            payload_name: PayloadName,
            ecosystem_uids: str | list[str] | None = None,
            ttl: int | None = None,
    ) -> bool:
        self.logger.debug(f"Requested to emit event '{payload_name}'.")
        payload = self.get_payload(payload_name, ecosystem_uids)
        if payload:
            try:
                result = await self.emit(payload_name, data=payload, ttl=ttl)
            except Exception as e:
                self.logger.error(
                    f"Encountered an error while emitting event '{payload_name}'. "
                    f"ERROR msg: `{e.__class__.__name__}: {e}`.")
            else:
                if result:
                    self.logger.debug(f"Payload for event '{payload_name}' sent.")
                else:
                    self.logger.warning(
                        f"Payload for event '{payload_name}' could not be sent.")
                return result
        else:
            self.logger.debug(f"No payload for event '{payload_name}' found.")
            return False

    async def on_turn_light(self, message: gv.TurnActuatorPayloadDict) -> None:
        message["actuator"] = gv.HardwareType.light
        await self.on_turn_actuator(message)

    async def on_turn_actuator(self, message: gv.TurnActuatorPayloadDict) -> None:
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
    async def on_change_management(self, message: gv.ManagementConfigPayloadDict) -> None:
        data: gv.ManagementConfigPayloadDict = self.validate_payload(
            message, gv.ManagementConfigPayload)
        ecosystem_uid: str = data["uid"]
        if ecosystem_uid in self.ecosystems:
            for management, status in data["data"].items():
                self.ecosystems[ecosystem_uid].config.set_management(management, status)
            self.engine.config.save(ConfigType.ecosystems)
            await self.send_payload("management", ecosystem_uids=[ecosystem_uid])

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

        event_name: CrudEventName = cast(CrudEventName, f"{action.name}_{target}")

        crud_link = crud_links_dict.get(event_name)
        if crud_link is None:
            raise ValueError(
                f"{action.name.capitalize()} {target} is not possible for this "
                f"engine.")

        if target in ("management", "time_parameters", "chaos_config"):
            # Need to update a setter
            def get_attr_setter(config: EcosystemConfig, attr_name: str) -> Callable:
                def inner(**value: dict):
                    setattr(config, attr_name, value)

                return inner

            return get_attr_setter(base_obj, crud_link.func_or_attr_name)
        else:
            def get_function(obj: Any, func_name: str) -> Callable:
                return getattr(obj, func_name)

            return get_function(base_obj, crud_link.func_or_attr_name)

    async def on_crud(self, message: gv.CrudPayloadDict) -> None:
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
        ecosystem_uid: str | None
        if target in ("ecosystem", "place"):
            ecosystem_uid = None
        else:
            ecosystem_uid: str = data["routing"]["ecosystem_uid"]
        event_name: CrudEventName = cast(CrudEventName, f"{action.name}_{target}")
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
            await self.emit(
                event="crud_result",
                data=gv.RequestResult(
                    uuid=crud_uuid,
                    status=gv.Result.failure,
                    message=str(e)
                ).model_dump()
            )
            return
        else:
            await self.emit(
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
        crud_link = crud_links_dict[event_name]
        if not crud_link.payload_name:
            self.logger.warning(
                f"No CRUD payload linked to action '{action.name} {target}' "
                f"was found. Updated data won't be sent to Ouranos.")
        payload_name = crud_link.payload_name
        await self.send_payload(payload_name=payload_name, ecosystem_uids=ecosystem_uid)

    async def send_buffered_data(self) -> None:
        if not self.use_db:
            raise RuntimeError(
                "The database is not enabled. To enable it, set configuration "
                "parameter 'USE_DATABASE' to 'True'.")
        SensorBuffer = self.sensor_buffer_cls  # noqa
        with self.db.scoped_session() as session:
            for payload in SensorBuffer.get_buffered_data(session):
                payload_dict: gv.BufferedSensorsDataPayloadDict = payload.model_dump()
                await self.emit(event="buffered_sensors_data", data=payload_dict)

    async def on_buffered_data_ack(self, message: gv.RequestResultDict) -> None:
        if not self.use_db:
            raise RuntimeError(
                "The database is not enabled. To enable it, set configuration "
                "parameter 'USE_DATABASE' to 'True'.")
        data: gv.RequestResultDict = self.validate_payload(
            message, gv.RequestResult)
        SensorBuffer = self.sensor_buffer_cls  # noqa
        with self.db.scoped_session() as session:
            if data["status"] == gv.Result.success:
                SensorBuffer.clear_buffer(session, data["uuid"])
            else:
                SensorBuffer.clear_uuid(session, data["uuid"])
