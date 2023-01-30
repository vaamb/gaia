from __future__ import annotations

from datetime import datetime
import logging
from threading import Thread
from time import sleep
import typing as t

from gaia.config import get_config
from gaia.shared_resources import scheduler
from gaia.utils import encrypted_uid, generate_uid_token


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.ecosystem import Ecosystem


if get_config().USE_DATABASE:
    from sqlalchemy import select

    from gaia.database import SQLAlchemyWrapper
    from gaia.database.models import SensorHistory


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
        self.db: SQLAlchemyWrapper | None
        if get_config().USE_DATABASE:
            self.db = SQLAlchemyWrapper(get_config())
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
                f"Encountered an error while sending light data. "
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
        elif self.type == "dispatcher":
            data = {"engine_uid": get_config().UUID}
        else:
            raise TypeError("Event type is invalid")
        self.emit("register_engine", data=data)

    def initialize_data_transfer(self) -> None:
        if not self._background_task:
            thread = Thread(target=self.background_task)
            thread.name = "ping"
            thread.start()
            self._thread = thread
            self._background_task = True
        self.send_config()
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

    def _get_uid_list(self, ecosystem_uids: list[str] | None = None) -> list[str]:
        if ecosystem_uids is None:
            return [e_uid for e_uid in self.ecosystems.keys()]
        else:
            return [e_uid for e_uid in ecosystem_uids
                    if e_uid in self.ecosystems.keys()]

    def _get_specific_config(
            self,
            config_type: str,
            ecosystem_uids: list[str] | None = None
    ) -> list[dict]:
        uids = self._get_uid_list(ecosystem_uids)
        rv = []
        for uid in uids:
            data = getattr(self.ecosystems[uid], config_type)
            if data:
                data.update({"uid": uid})
                rv.append(data)
        return rv

    def send_config(self, ecosystem_uids: list[str] | None = None) -> None:
        self.logger.debug("Received send_config event")
        uids = self._get_uid_list(ecosystem_uids)
        [self.emit(cfg, data=self._get_specific_config(cfg, uids)) for cfg in
         ("base_info", "management", "environmental_parameters", "hardware")]

    def _get_data(
            self,
            data_type: str,
            ecosystem_uids: list[str] | None = None
    ) -> list:
        rv = []
        for uid in self._get_uid_list(ecosystem_uids):
            try:
                data = getattr(self.ecosystems[uid], data_type)
                if data:
                    rv.append({**{"ecosystem_uid": uid}, **data})
            # Except when subroutines are still loading or received a message
            #  for an ecosystem not on this engine
            except KeyError:
                pass
        return rv

    def send_sensors_data(self, ecosystem_uids: list[str] | None = None) -> None:
        self.logger.debug("Received send_sensors_data event")
        data = self._get_data("sensors_data", ecosystem_uids=ecosystem_uids)
        if data:
            self.emit("sensors_data", data=data)

    def send_health_data(self, ecosystem_uids: list[str] | None = None) -> None:
        self.logger.debug("Received send_health_data event")
        data = self._get_data("plants_health", ecosystem_uids=ecosystem_uids)
        if data:
            self.emit("health_data", data=data)

    def send_light_data(self, ecosystem_uids: list[str] | None = None) -> None:
        self.logger.debug("Received send_light_data event")
        data = self._get_data("light_info", ecosystem_uids=ecosystem_uids)
        if data:
            self.emit("light_data", data=data)

    def on_turn_light(self, message: dict) -> None:
        self.logger.debug("Received turn_light event, sending to turn_actuator")
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
                self.send_light_data([ecosystem_uid])

    def on_change_management(self, message: dict) -> None:
        ecosystem_uid: str = message["ecosystem"]
        management: str = message["management"]
        status: bool = message["status"]
        if ecosystem_uid in self.ecosystems:
            self.ecosystems[ecosystem_uid].config.set_management(management, status)
            self.ecosystems[ecosystem_uid].config.save()
            self.emit(
                "management",
                data=self._get_specific_config("management", [ecosystem_uid])
            )

    def on_get_data_since(self, message: dict) -> None:
        if self.db is not None:
            ecosystem_uids: list[str] = message["ecosystems"]
            uids: list[str] = self._get_uid_list(ecosystem_uids)
            since_str: str = message["since"]
            since: datetime = datetime.fromisoformat(since_str).astimezone()
            with self.db.scoped_session() as session:
                query = (
                    select(SensorHistory)
                        .where(SensorHistory.datetime >= since)
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
