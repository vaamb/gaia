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
from typing import Callable, cast, Iterator, Literal, NamedTuple, Type, TypeVar
from uuid import UUID

from pydantic import RootModel, ValidationError

from dispatcher import AsyncEventHandler
import gaia_validators as gv

from gaia import Ecosystem, Engine
from gaia.config.from_files import ConfigType
from gaia.dependencies.camera import SerializableImagePayload
from gaia.ecosystem import _EcosystemPayloads
from gaia.utils import humanize_list, local_ip_address


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia_validators.image import SerializableImage
    from sqlalchemy_wrapper import SQLAlchemyWrapper


PT = TypeVar("PT", dict, list[dict])


ENGINE_PAYLOADS: frozenset[PayloadName] = frozenset({"places_list"})
HEARTBEAT_TIMEOUT: float = 30.0
PING_INTERVAL: float = 15.0


PayloadName = Literal[
    "actuators_data",
    "base_info",
    "chaos_parameters",
    "climate",
    "hardware",
    "health_data",
    "management",
    "nycthemeral_info",
    "places_list",
    "plants",
    "sensors_data",
    "weather",
]


payload_classes_dict: dict[PayloadName, Type[gv.EcosystemPayload]] = {
    "actuators_data": gv.ActuatorsDataPayload,
    "base_info": gv.BaseInfoConfigPayload,
    "chaos_parameters": gv.ChaosParametersPayload,
    "climate": gv.ClimateConfigPayload,
    "hardware": gv.HardwareConfigPayload,
    "health_data": gv.HealthDataPayload,
    "management": gv.ManagementConfigPayload,
    "nycthemeral_info": gv.NycthemeralCycleInfoPayload,
    "places_list": gv.PlacesPayload,
    "plants": gv.PlantConfigPayload,
    "sensors_data": gv.SensorsDataPayload,
    "weather": gv.WeatherConfigPayload,
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
    "create_weather_event",
    "update_weather_event",
    "delete_weather_event",
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
    "create_weather_event": CrudLinks(
        "set_weather_parameter", "weather"),
    "update_weather_event": CrudLinks(
        "update_weather_parameter", "weather"),
    "delete_weather_event": CrudLinks(
        "delete_weather_parameter", "weather"),
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
                msg_list = [f"{error['loc'][0] if error['loc'] else 'root'}: {error['msg']}" for error in e.errors()]
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
            and monotonic() - self._last_heartbeat < HEARTBEAT_TIMEOUT
        )

    @property
    def ecosystems(self) -> dict[str, Ecosystem]:
        return self.engine.ecosystems

    @staticmethod
    def _format_error(e: Exception) -> str:
      return f"{e.__class__.__name__}: {e}"

    # ---------------------------------------------------------------------------
    #   Background jobs
    # ---------------------------------------------------------------------------
    def _start_ping_task(self) -> None:
        if self._ping_task is not None:
            self._ping_task.cancel()
        self._ping_task = asyncio.create_task(self._ping_loop(), name="events-ping")

    async def _ping_loop(self) -> None:
        while True:
            start = monotonic()
            await self.ping()
            sleep_time = max(PING_INTERVAL - (monotonic() - start), 0.01)
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
                    f"{self._format_error(e)}."
                )

    async def on_pong(self) -> None:
        self.logger.debug("Received 'pong'.")
        self._last_heartbeat = monotonic()

    # ---------------------------------------------------------------------------
    #   Data payloads retrieval and sending
    # ---------------------------------------------------------------------------
    def filter_uids(self, ecosystem_uids: str | list[str] | None = None) -> list[str]:
        if ecosystem_uids is None:
            return list(self.ecosystems)
        else:
            if isinstance(ecosystem_uids, str):
                ecosystem_uids = [ecosystem_uids]
            return [uid for uid in ecosystem_uids if uid in self.ecosystems]

    def get_payload(
            self,
            payload_name: PayloadName,
            ecosystem_uids: str | list[str] | None = None,
    ) -> gv.EcosystemPayloadDict | list[gv.EcosystemPayloadDict] | None:
        self.logger.debug(f"Getting '{payload_name}' payload.")
        if payload_name in ENGINE_PAYLOADS:
            return self._get_engine_payload(payload_name)
        else:
            return self._get_ecosystem_payload(payload_name, ecosystem_uids)

    def _get_ecosystem_payload(
            self,
            payload_name: PayloadName,
            ecosystem_uids: str | list[str] | None = None,
    ) -> list[gv.EcosystemPayloadDict] | None:
        # Check that the event is possible
        if not hasattr(_EcosystemPayloads, payload_name):
            self.logger.error(f"Payload for event '{payload_name}' is not defined.")
            return None
        # Get the data
        rv: list[gv.EcosystemPayloadDict] = []
        uids = self.filter_uids(ecosystem_uids)
        self.logger.debug(
            f"Getting '{payload_name}' payload for {humanize_list(uids)}.")
        for uid in uids:
            data = getattr(self.ecosystems[uid]._payloads, payload_name)
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
                    f"{self._format_error(e)}.")
                return False
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
        await self.send_payload("chaos_parameters", uids)
        await self.send_payload("nycthemeral_info", uids)
        await self.send_payload("climate", uids)
        await self.send_payload("weather", uids)
        await self.send_payload("hardware", uids)
        await self.send_payload("plants", uids)
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

    @validate_payload(RootModel[dict | None])
    async def on_connect(self, environment: dict | None) -> None:  # noqa
        self.logger.info(
            "Connection to the message broker successful. Will try to register "
            "the engine to Ouranos.")
        self._start_ping_task()
        await self.register()

    async def on_disconnect(self, *args) -> None:  # noqa
        self.logger.debug("Received a disconnection request.")
        if self._ping_task is not None:
            self._ping_task.cancel()
            self._ping_task = None
        if self.engine.stopped:
            self.logger.info("Engine requested to disconnect from the broker.")
            return  # The Engine takes care to shut down the scheduler and the jobs running
        elif self.registered:
            self.logger.warning("Dispatcher disconnected from the broker.")
        else:
            self.logger.error("Failed to register engine.")

    async def on_register(self) -> None:
        self.registered = False
        self.logger.info("Received registration request from Ouranos.")
        await sleep(0.25)  # Allow to finish engine initialization in some cases
        await self.register()

    @validate_payload(RootModel[str])
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

    @validate_payload(RootModel[list | None])
    async def on_initialization_ack(self, missing_data: list | None = None) -> None:
        if missing_data is None:
            self.registered = True
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
                actuator=data.get("group") or data["actuator"],
                mode=data["mode"],
                level=data.get("level", 100.0),
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
            base_obj = self.engine.config.get_ecosystem_config(ecosystem_uid)

        event_name: CrudEventName = cast(CrudEventName, f"{action.name}_{target}")

        crud_link = crud_links_dict.get(event_name)
        if crud_link is None:
            raise ValueError(
                f"{action.name.capitalize()} {target} is not possible for this "
                f"engine.")

        if target in ("management", "chaos_config"):
            # Need to update a setter
            def attr_setter(**value: dict) -> None:
                setattr(base_obj, crud_link.func_or_attr_name, value)

            return attr_setter
        else:
            return getattr(base_obj, crud_link.func_or_attr_name)

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
            ecosystem_uid = data["routing"]["ecosystem_uid"]
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
                f"`{crud_uuid}`. {self._format_error(e)}.")
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
        await self.send_payload(
            payload_name=crud_link.payload_name, ecosystem_uids=ecosystem_uid)

    # ---------------------------------------------------------------------------
    #   Events for buffered data
    # ---------------------------------------------------------------------------
    async def send_buffered_data(self) -> None:
        if not self.use_db:
            raise RuntimeError(
                "The database is not enabled. To enable it, set configuration "
                "parameter 'USE_DATABASE' to 'True'.")
        from gaia.database.models import ActuatorBuffer, SensorBuffer

        buffers = [
            (SensorBuffer, "buffered_sensors_data"),
            (ActuatorBuffer, "buffered_actuators_data"),
        ]
        async with self.db.scoped_session() as session:
            for buffer_cls, event_name in buffers:
                async for payload in await buffer_cls.get_buffered_data(session):
                    await self.emit(event=event_name, data=payload.model_dump())

    @validate_payload(gv.RequestResult)
    async def on_buffered_data_ack(self, data: gv.RequestResultDict) -> None:
        if not self.use_db:
            raise RuntimeError(
                "The database is not enabled. To enable it, set configuration "
                "parameter 'USE_DATABASE' to 'True'.")
        from gaia.database.models import ActuatorBuffer, DataBufferMixin, SensorBuffer

        async with self.db.scoped_session() as session:
            if data["status"] == gv.Result.success:
                for db_model in (ActuatorBuffer, SensorBuffer):
                    db_model: DataBufferMixin
                    await db_model.mark_exchange_as_success(session, data["uuid"])
            else:
                for db_model in (ActuatorBuffer, SensorBuffer):
                    self.logger.error(
                        f"Encountered an error while treating buffered data "
                        f"exchange `{data['uuid']}`. ERROR msg: `{data['message']}`.")
                    await db_model.mark_exchange_as_failed(session, data["uuid"])

    # ---------------------------------------------------------------------------
    #   Pictures
    # ---------------------------------------------------------------------------
    def _iter_picture_arrays(
            self,
            ecosystem_uids: str | list[str] | None = None,
    ) -> Iterator[tuple[str, list[SerializableImage]]]:
        uids = self.filter_uids(ecosystem_uids)
        self.logger.debug(f"Getting 'picture_arrays' for {humanize_list(uids)}.")
        for uid in uids:
            picture_arrays = self.ecosystems[uid].picture_arrays
            if isinstance(picture_arrays, gv.Empty):
                continue
            yield uid, picture_arrays

    async def send_picture_arrays(
            self,
            ecosystem_uids: str | list[str] | None = None,
    ) -> None:
        for uid, picture_arrays in self._iter_picture_arrays(ecosystem_uids):
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
                f"ERROR msg: `{e.__class__.__name__}: {e}`."
            )

    async def upload_picture_arrays(
            self,
            ecosystem_uids: str | list[str] | None = None,
    ) -> None:
        if self.camera_token is None:
            self.logger.error("No camera token found, cannot send picture arrays.")
            return
        for uid, picture_arrays in self._iter_picture_arrays(ecosystem_uids):
            for image in picture_arrays:
                image.metadata["ecosystem_uid"] = uid
                await self._upload_image(image)
