from __future__ import annotations

from gaia.dependencies import check_dependencies

check_dependencies("dispatcher")

import asyncio
from asyncio import sleep, Task
from datetime import datetime, timezone
from functools import wraps
import inspect
import logging
from time import monotonic
import typing as t
from typing import Any, Callable, cast, Literal, NamedTuple, Type, TypeVar
from uuid import UUID

from pydantic import RootModel, ValidationError

from dispatcher import AsyncEventHandler
import gaia_validators as gv

from gaia import Ecosystem, EcosystemConfig, Engine
from gaia.config.from_files import ConfigType
from gaia.dependencies.camera import SerializableImagePayload
from gaia.utils import humanize_list, local_ip_address


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia_validators.image import SerializableImage
    from sqlalchemy_wrapper import SQLAlchemyWrapper


PT = TypeVar("PT", dict, list[dict])


PayloadName = Literal[
    "actuators_data",
    "base_info",
    "chaos_parameters",
    "climate",
    "environmental_parameters",
    "hardware",
    "health_data",
    "light_data",
    "management",
    "nycthemeral_config",
    "nycthemeral_info",
    "places_list",
    "sensors_data",
]


payload_classes_dict: dict[PayloadName, Type[gv.EcosystemPayload]] = {
    "base_info": gv.BaseInfoConfigPayload,
    "management": gv.ManagementConfigPayload,
    "environmental_parameters": gv.EnvironmentConfigPayload,
    "chaos_parameters": gv.ChaosParametersPayload,
    "nycthemeral_config": gv.NycthemeralCycleConfigPayload,
    "light_data": gv.LightDataPayload,
    "nycthemeral_info": gv.NycthemeralCycleInfoPayload,
    "climate": gv.ClimateConfigPayload,
    "hardware": gv.HardwareConfigPayload,
    "sensors_data": gv.SensorsDataPayload,
    "health_data": gv.HealthDataPayload,
    "actuators_data": gv.ActuatorsDataPayload,
    "places_list": gv.PlacesPayload,
}


class CrudLinks(NamedTuple):
    func_or_attr_name: str
    payload_name: PayloadName


CrudEventName = Literal[
    "create_ecosystem",
    "update_ecosystem",
    "delete_ecosystem",
    "create_place",
    "update_place",
    "delete_place",
    "update_management",
    "update_chaos_config",
    "update_nycthemeral_config",
    "create_climate_parameter",
    "update_climate_parameter",
    "delete_climate_parameter",
    "create_hardware",
    "update_hardware",
    "delete_hardware",
]


crud_links_dict: dict[CrudEventName, CrudLinks] = {
    # Ecosystem creation and deletion
    "create_ecosystem": CrudLinks("create_ecosystem", "base_info"),
    "update_ecosystem": CrudLinks("update_ecosystem_base_info", "base_info"),
    "delete_ecosystem": CrudLinks("delete_ecosystem", "base_info"),
    # Places creation, update and deletion
    "create_place": CrudLinks("set_place", "places_list"),
    "update_place": CrudLinks("update_place", "places_list"),
    "delete_place": CrudLinks("delete_place", "places_list"),
    # Ecosystem properties update
    "update_management": CrudLinks("managements", "management"),
    # Environment parameter creation, deletion and update
    "update_chaos_config": CrudLinks("chaos_config", "chaos_parameters"),
    "update_nycthemeral_config": CrudLinks("set_nycthemeral_cycle", "nycthemeral_info"),
    "create_climate_parameter": CrudLinks(
        "set_climate_parameter", "climate"),
    "update_climate_parameter": CrudLinks(
        "update_climate_parameter", "climate"),
    "delete_climate_parameter": CrudLinks(
        "delete_climate_parameter", "climate"),
    # Hardware creation, deletion and update
    "create_hardware": CrudLinks("create_new_hardware", "hardware"),
    "update_hardware": CrudLinks("update_hardware", "hardware"),
    "delete_hardware": CrudLinks("delete_hardware", "hardware"),
}


def validate_payload(model_cls: Type[gv.BaseModel] | Type[RootModel]):
    """Decorator which validate and parse data payload before calling the event
    and the remaining decorators"""
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(self: Events, data: PT, *args):
            try:
                validated_data = model_cls.model_validate(data).model_dump(by_alias=True)
            except ValidationError as e:
                event: str = func.__name__[3:]
                msg_list = [f"{error['loc'][0]}: {error['msg']}" for error in e.errors()]
                self.logger.error(
                    f"Encountered an error while validating '{event}' data. Error "
                    f"msg: {', '.join(msg_list)}"
                )
                raise
            return await func(self, validated_data, *args)
        return wrapper
    return decorator


class Events(AsyncEventHandler):
    """A class holding all the events coming from event-dispatcher

    :param engine: an `Engine` instance
    """
    def __init__(self, engine: Engine, **kwargs) -> None:
        kwargs["namespace"] = "aggregator"
        super().__init__(**kwargs)
        self.engine: Engine = engine
        self.ecosystems: dict[str, "Ecosystem"] = self.engine.ecosystems
        self.registered = False
        self._resent_initialization_data: bool = False
        self._last_heartbeat: float = monotonic()
        self._ping_task: Task | None = None
        self._jobs_scheduled: bool = False
        self.camera_token: str | None = None
        app_config = self.engine.config.app_config
        self._compression_format: str | None = app_config.PICTURE_COMPRESSION_FORMAT
        self._resize_ratio: float = app_config.PICTURE_RESIZE_RATIO
        self.logger = logging.getLogger("gaia.engine.events_handler")

    @property
    def use_db(self) -> bool:
        return self.engine.use_db

    @property
    def db(self) -> SQLAlchemyWrapper:
        return self.engine.db

    def is_connected(self) -> bool:
        return (
            self._dispatcher.connected
            and monotonic() - self._last_heartbeat < 30.0
        )

    # ---------------------------------------------------------------------------
    #   Background jobs
    # ---------------------------------------------------------------------------
    def _schedule_jobs(self) -> None:
        self._jobs_scheduled = True

    def _unschedule_jobs(self) -> None:
        self._jobs_scheduled = False

    def _start_ping_task(self) -> None:
        self._ping_task = asyncio.create_task(self._ping_loop(), name="events-ping")

    async def _ping_loop(self) -> None:
        while True:
            start = monotonic()
            await self.ping()
            sleep_time = max(15 - (monotonic() - start), 0.01)
            await sleep(sleep_time)

    async def ping(self) -> None:
        if self._dispatcher.connected:
            try:
                ping_data: gv.EnginePingPayloadDict = {
                    "engine_uid": self.engine.uid,
                    "timestamp": datetime.now(timezone.utc),
                    "ecosystems": [
                        {
                            "uid": ecosystem.uid,
                            "status": ecosystem.started,
                        }
                        for ecosystem in self.ecosystems.values()
                    ]
                }
                self.logger.debug("Sending 'ping'.")
                await self.emit("ping", data=ping_data, namespace="aggregator-stream")
            except Exception as e:
                self.logger.error(
                    f"Encountered an error while running the ping routine. "
                    f"ERROR msg: `{e.__class__.__name__} :{e}`."
                )

    async def on_pong(self) -> None:
        self.logger.debug("Received 'pong'.")
        self._last_heartbeat = monotonic()

    # ---------------------------------------------------------------------------
    #   Data payloads retrieval and sending
    # ---------------------------------------------------------------------------
    def filter_uids(self, ecosystem_uids: str | list[str] | None = None) -> list[str]:
        if ecosystem_uids is None:
            return [uid for uid in self.ecosystems.keys()]
        else:
            if isinstance(ecosystem_uids, str):
                ecosystem_uids = [ecosystem_uids]
            return [uid for uid in ecosystem_uids if uid in self.ecosystems.keys()]

    def get_payload(
            self,
            payload_name: PayloadName,
            ecosystem_uids: str | list[str] | None = None,
    ) -> gv.EcosystemPayloadDict | list[gv.EcosystemPayloadDict] | None:
        self.logger.debug(f"Getting '{payload_name}' payload.")
        if payload_name in ("places_list",):
            return self._get_engine_payload(payload_name)
        else:
            return self._get_ecosystem_payload(payload_name, ecosystem_uids)

    def _get_ecosystem_payload(
            self,
            payload_name: PayloadName,
            ecosystem_uids: str | list[str] | None = None,
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
            payload_name: PayloadName,
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
        if payload_name == "picture_arrays":
            raise ValueError("'picture_arrays' need to be sent via a specific method.")
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

    async def send_payload_if_connected(
            self,
            payload_name: PayloadName,
            ecosystem_uids: str | list[str] | None = None,
            ttl: int | None = None,
    ) -> None:
        if not self.is_connected():
            self.logger.debug(
                f"Events handler not currently connected. Emission of event "
                f"'{payload_name}' aborted.")
            return
        await self.send_payload(payload_name, ecosystem_uids=ecosystem_uids, ttl=ttl)

    async def send_ecosystems_info(
            self,
            ecosystem_uids: str | list[str] | None = None,
    ) -> None:
        await self.send_payload("places_list")
        uids = self.filter_uids(ecosystem_uids)
        await self.send_payload("base_info", uids)
        await self.send_payload("management", uids)
        # await self.send_payload("environmental_parameters", uids)
        await self.send_payload("chaos_parameters", uids)
        await self.send_payload("nycthemeral_info", uids)
        await self.send_payload("climate", uids)
        await self.send_payload("hardware", uids)
        await self.send_payload("actuators_data", uids)

    # ---------------------------------------------------------------------------
    #   Events for connection and initial handshake
    # ---------------------------------------------------------------------------
    async def register(self) -> None:
        if self.engine.use_db:
            # Reset exchanges uuid as Ouranos could have failed through data exchange
            await self.engine._reset_db_exchanges_uuid()
        self._resent_initialization_data = False
        data = gv.EnginePayload(
            engine_uid=self.engine.config.app_config.ENGINE_UID,
            address=local_ip_address(),
        ).model_dump()
        result = await self.emit("register_engine", data=data, ttl=15)
        if result:
            self.logger.debug("Registration request sent.")
        else:
            self.logger.warning("Registration request could not be sent.")

    async def on_connect(self, environment) -> None:  # noqa
        self.logger.info(
            "Connection to the message broker successful. Will try to register "
            "the engine to Ouranos.")
        self._start_ping_task()
        await self.register()

    async def on_disconnect(self, *args) -> None:  # noqa
        self.logger.debug("Received a disconnection request.")
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
        await sleep(0.25)  # Allow to finish engine initialization in some cases
        await self.register()

    async def on_registration_ack(self, host_uid: str) -> None:
        try:
            uuid = UUID(host_uid)
        except ValueError:
            self.logger.warning(
                "Received a wrongly formatted registration acknowledgment.")
            return
        if self._dispatcher.host_uid != uuid:
            self.logger.warning(
                "Received a registration acknowledgment for another dispatcher.")
            return
        self.logger.info(
            "Engine registration successful, sending initial ecosystems info.")
        await self.send_initialization_data()

    @validate_payload(RootModel[str])
    async def on_camera_token(self, camera_token: str) -> None:
        self.logger.info("Received camera token from Ouranos.")
        self.camera_token = camera_token

    async def send_initialization_data(self) -> None:
        await self.send_ecosystems_info()
        self.logger.info("Initial ecosystems info sent.")
        await sleep(1.0)  # Allow Ouranos to handle all the initialization data
        await self.emit("initialization_data_sent")

    async def on_initialization_ack(self, missing_data: list | None = None) -> None:
        if missing_data is None:
            self.registered = True
            if not self._jobs_scheduled:
                self._schedule_jobs()
            self.logger.info("Ouranos successfully received ecosystems info.")
            if self.use_db:
                await self.send_buffered_data()
        else:
            self.logger.warning(
                f"Ouranos did not receive all the initial ecosystems info. "
                f"Non-received info: {humanize_list(missing_data)}.")
            if not self._resent_initialization_data:
                await self.send_initialization_data()
                self._resent_initialization_data = True
            else:
                await self.on_disconnect()

    # ---------------------------------------------------------------------------
    #   Events to modify managements and actuators state
    # ---------------------------------------------------------------------------
    @validate_payload(gv.TurnActuatorPayload)
    async def on_turn_light(self, data: gv.TurnActuatorPayloadDict) -> None:
        data["actuator"] = gv.HardwareType.light
        await self.on_turn_actuator(data)

    @validate_payload(gv.TurnActuatorPayload)
    async def on_turn_actuator(self, data: gv.TurnActuatorPayloadDict) -> None:
        ecosystem_uid: str = data["ecosystem_uid"]
        self.logger.debug(
            f"Received 'turn_actuator' event to turn ecosystem '{ecosystem_uid}'"
            f"'s '{data['actuator'].name}' to mode '{data['mode'].name}'.")
        if ecosystem_uid in self.ecosystems:
            await self.ecosystems[ecosystem_uid].turn_actuator(
                actuator=data["actuator"],
                mode=data["mode"],
                countdown=data.get("countdown", 0.0),
            )

    @validate_payload(gv.ManagementConfigPayload)
    async def on_change_management(self, data: gv.ManagementConfigPayloadDict) -> None:
        ecosystem_uid: str = data["uid"]
        if ecosystem_uid in self.ecosystems:
            for management, status in data["data"].items():
                self.ecosystems[ecosystem_uid].config.set_management(management, status)
            await self.engine.config.save(ConfigType.ecosystems)
            await self.send_payload("management", ecosystem_uids=[ecosystem_uid])

    # ---------------------------------------------------------------------------
    #   Events for CRUD requests
    # ---------------------------------------------------------------------------
    def _get_crud_function(
            self,
            action: gv.CrudAction,
            target: str,
            ecosystem_uid: str | None = None,
    ) -> Callable:
        if target in ("ecosystem", "place"):
            base_obj = self.engine.config
        else:
            if ecosystem_uid is None:
                raise ValueError(
                    f"{action.name.capitalize()} {target} requires the "
                    f"'ecosystem_uid' field to be set.")
            if ecosystem_uid not in self.engine.ecosystems:
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

    @validate_payload(gv.CrudPayload)
    async def on_crud(self, data: gv.CrudPayloadDict) -> None:
        # Verify it is the intended recipient
        engine_uid = data["routing"]["engine_uid"]
        if engine_uid != self.engine.uid:
            self.logger.warning(
                f"Received a CRUD request intended to engine '{engine_uid}'.")
            return

        # Extract CRUD request data
        crud_uuid: UUID = data["uuid"]
        action: gv.CrudAction = data["action"]
        target: str = data["target"]
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
            result = crud_function(**data["data"])
            if inspect.isawaitable(result):
                result = await result
            await self.engine.config.save(ConfigType.ecosystems)
        except Exception as e:
            self.logger.error(
                f"Encountered an error while treating CRUD request "
                f"`{crud_uuid}`. ERROR msg: `{e.__class__.__name__}: {e}`.")
            await self.emit(
                event="crud_result",
                data=gv.RequestResult(
                    uuid=crud_uuid,
                    status=gv.Result.failure,
                    message=str(e),
                ).model_dump(),
            )
            return
        else:
            await self.emit(
                event="crud_result",
                data=gv.RequestResult(
                    uuid=crud_uuid,
                    status=gv.Result.success,
                ).model_dump(),
            )
            self.logger.info(f"CRUD request '{crud_uuid}' was successfully treated.")

        # Send back the updated info
        await self.engine.refresh_ecosystems(send_info=False)
        crud_link = crud_links_dict[event_name]
        if not crud_link.payload_name:
            self.logger.warning(
                f"No CRUD payload linked to action '{action.name} {target}' "
                f"was found. Updated data won't be sent to Ouranos.")
        payload_name = crud_link.payload_name
        await self.send_payload(payload_name=payload_name, ecosystem_uids=ecosystem_uid)

    # ---------------------------------------------------------------------------
    #   Events for buffered data
    # ---------------------------------------------------------------------------
    async def send_buffered_data(self) -> None:
        if not self.use_db:
            raise RuntimeError(
                "The database is not enabled. To enable it, set configuration "
                "parameter 'USE_DATABASE' to 'True'.")
        from gaia.database.models import ActuatorBuffer, HealthBuffer, SensorBuffer

        async with self.db.scoped_session() as session:
            sensor_buffer_iterator = await SensorBuffer.get_buffered_data(session)
            async for payload in sensor_buffer_iterator:
                payload_dict: gv.BufferedSensorsDataPayloadDict = payload.model_dump()
                await self.emit(event="buffered_sensors_data", data=payload_dict)
            health_buffer_iterator = await HealthBuffer.get_buffered_data(session)
            async for payload in health_buffer_iterator:
                payload_dict: gv.BufferedHealthRecordPayloadDict = payload.model_dump()
                await self.emit(event="buffered_health_data", data=payload_dict)
            actuator_buffer_iterator = await ActuatorBuffer.get_buffered_data(session)
            async for payload in actuator_buffer_iterator:
                payload_dict: gv.BufferedActuatorsStatePayloadDict = payload.model_dump()
                await self.emit(event="buffered_actuators_data", data=payload_dict)

    @validate_payload(gv.RequestResult)
    async def on_buffered_data_ack(self, data: gv.RequestResultDict) -> None:
        if not self.use_db:
            raise RuntimeError(
                "The database is not enabled. To enable it, set configuration "
                "parameter 'USE_DATABASE' to 'True'.")
        from gaia.database.models import ActuatorBuffer, DataBufferMixin, SensorBuffer

        async with self.db.scoped_session() as session:
            for db_model in (ActuatorBuffer, SensorBuffer):
                db_model: DataBufferMixin
                if data["status"] == gv.Result.success:
                    await db_model.mark_exchange_as_success(session, data["uuid"])
                else:
                    self.logger.error(
                        f"Encountered an error while treating buffered data "
                        f"exchange `{data['uuid']}`. ERROR msg: "
                        f"`{data['message']}`.")
                    await db_model.mark_exchange_as_failed(session, data["uuid"])

    # ---------------------------------------------------------------------------
    #   Pictures
    # ---------------------------------------------------------------------------
    async def send_picture_arrays(
            self,
            ecosystem_uids: str | list[str] | None = None,
    ) -> None:
        uids = self.filter_uids(ecosystem_uids)
        self.logger.debug(f"Getting 'picture_arrays' for {humanize_list(uids)}.")

        for uid in uids:
            picture_arrays = self.ecosystems[uid].picture_arrays
            if isinstance(picture_arrays, gv.Empty):
                continue
            if self._resize_ratio != 1.0:
                picture_arrays = [
                    picture_array.resize(ratio=self._resize_ratio)
                    for picture_array in picture_arrays
                ]
            ecosystem_payload = SerializableImagePayload(
                uid=uid,
                data=picture_arrays,
            )
            await self.emit(
                "picture_arrays",
                data=ecosystem_payload.serialize(
                    compression_format=self._compression_format),
                namespace="aggregator-stream",
            )

    async def _upload_image(self, image: "SerializableImage") -> None:
        from aiohttp import ClientSession
        # Format data
        if self._resize_ratio != 1.0:
            image = image.resize(ratio=self._resize_ratio)
        to_send = image.serialize(compression_format=self._compression_format)
        headers = {"token": self.camera_token}
        base_url = self.engine.config.app_config.AGGREGATOR_SERVER_URL
        url = f"{base_url}/upload_camera_image"
        # Upload data
        try:
            async with ClientSession(headers=headers) as session:
                async with session.post(url, data=to_send, timeout=3.0) as resp:
                    response = await resp.json()
                    self.logger.debug(f"Image sent. Response: {response}")
        except Exception as e:
            self.logger.error(
                f"Encountered an error while uploading image. "
                f"ERROR msg: `{e.__class__.__name__} :{e}`."
            )

    async def upload_picture_arrays(
            self,
            ecosystem_uids: str | list[str] | None = None,
    ) -> None:
        if self.camera_token is None:
            self.logger.error("No camera token found, cannot send picture arrays.")
            return
        uids = self.filter_uids(ecosystem_uids)
        self.logger.debug(f"Getting 'picture_arrays' for {humanize_list(uids)}.")

        for uid in uids:
            picture_arrays = self.ecosystems[uid].picture_arrays
            if isinstance(picture_arrays, gv.Empty):
                continue
            for image in picture_arrays:
                image: SerializableImage
                image.metadata["ecosystem_uid"] = uid
                await self._upload_image(image)
