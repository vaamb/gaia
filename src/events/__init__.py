from datetime import datetime
import logging
from threading import Thread
from time import sleep
import typing as t

from config import Config
from src.shared_resources import scheduler
from src.utils import encrypted_uid, generate_uid_token


if t.TYPE_CHECKING:  # pragma: no cover
    from src.ecosystem import Ecosystem


if Config.USE_DATABASE:
    from sqlalchemy import select

    from src.database import SQLAlchemyWrapper
    from src.database.models import SensorHistory


logger = logging.getLogger(f"{Config.APP_NAME.lower()}.broker")


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
        if Config.USE_DATABASE:
            from src.database import SQLAlchemyWrapper
            self.db = SQLAlchemyWrapper(Config)
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
                logger.debug(log_msg)
            else:
                logger.error(log_msg)

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
            data = {"engine_uid": Config.UUID}
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
        logger.info("Connection successful")
        self.register()

    def on_disconnect(self, *args) -> None:
        if self._registered:
            logger.warning("Disconnected from server")
        else:
            logger.error("Failed to register engine")

    def on_register(self, *args):
        self._registered = False
        logger.info("Received registration request from server")
        self.register()

    def on_register_ack(self, *args) -> None:
        logger.info("Engine registration successful")
        self._registered = True
        self.initialize_data_transfer()

    def _get_uid_list(self, ecosystem_uids: t.Union[str, tuple] = "all") -> list:
        if isinstance(ecosystem_uids, str):
            ecosystem_uids = ecosystem_uids.split(",")
        if "all" in ecosystem_uids:
            return [e_uid for e_uid in self.ecosystems.keys()]
        else:
            return [e_uid for e_uid in ecosystem_uids
                    if e_uid in self.ecosystems.keys()]

    def _get_specific_config(
            self,
            config_type: str,
            ecosystem_uids: t.Union[str, tuple, list] = "all"
    ) -> list[dict]:
        uids = self._get_uid_list(ecosystem_uids)
        rv = []
        for uid in uids:
            data = getattr(self.ecosystems[uid], config_type)
            if data:
                data.update({"uid": uid})
                rv.append(data)
        return rv

    def send_config(self, ecosystem_uids: t.Union[str, tuple] = "all") -> None:
        logger.debug("Received send_config event")
        uids = self._get_uid_list(ecosystem_uids)
        [self.emit(cfg, data=self._get_specific_config(cfg, uids)) for cfg in
         ("base_info", "management", "environmental_parameters", "hardware")]

    def _get_data(
            self,
            data_type: str,
            ecosystem_uids: t.Union[str, tuple, list] = "all"
    ) -> list:
        rv = []
        for uid in self._get_uid_list(ecosystem_uids):
            try:
                data = getattr(self.ecosystems[uid], data_type)
                if data:
                    rv.append({**{"ecosystem_uid": uid}, **data})
            # Except when subroutines are still loading
            except KeyError:
                pass
        return rv

    def send_sensors_data(self, ecosystem_uids: t.Union[str, tuple] = "all") -> None:
        logger.debug("Received send_sensors_data event")
        data = self._get_data("sensors_data", ecosystem_uids=ecosystem_uids)
        if data:
            self.emit("sensors_data", data=data)

    def send_health_data(self, ecosystem_uids: t.Union[str, tuple] = "all") -> None:
        logger.debug("Received send_health_data event")
        data = self._get_data("plants_health", ecosystem_uids=ecosystem_uids)
        if data:
            self.emit("health_data", data=data)

    def send_light_data(self, ecosystem_uids: t.Union[str, tuple] = "all") -> None:
        logger.debug("Received send_light_data event")
        data = self._get_data("light_info", ecosystem_uids=ecosystem_uids)
        if data:
            self.emit("light_data", data=data)

    def on_turn_light(self, message: dict) -> None:
        logger.debug("Received turn_light event")
        ecosystem_uid: str = message["ecosystem"]
        mode: str = message["mode"]
        countdown: float = message.get("countdown", 0)
        try:
            self.ecosystems[ecosystem_uid].turn_actuator(
                "light", mode=mode, countdown=countdown
            )
            self.send_light_data(ecosystem_uid)
        # Except when subroutines are still loading
        except KeyError:
            print(f"{ecosystem_uid}'s light subroutine has not initialized yet")

    def on_turn_actuator(self, message: dict) -> None:
        logger.debug("Received turn_actuator event")
        ecosystem_uid: str = message["ecosystem"]
        actuator: str = message["actuator"]
        mode: str = message["mode"]
        countdown: float = message.get("countdown", 0.0)
        try:
            self.ecosystems[ecosystem_uid].turn_actuator(
                actuator=actuator, mode=mode, countdown=countdown
            )
        # Except when subroutines are still loading
        except KeyError:
            print(f"{ecosystem_uid}'s {actuator} cannot be turned to {mode} yet")
        finally:
            if actuator == "light":
                self.send_light_data(ecosystem_uid)

    def on_change_management(self, message: dict) -> None:
        ecosystem_uid: str = message["ecosystem"]
        management: str = message["management"]
        status: bool = message["status"]
        try:
            self.ecosystems[ecosystem_uid].config.set_management(management, status)
            self.ecosystems[ecosystem_uid].config.save()
            self.emit(
                "management",
                data=self._get_specific_config("management", ecosystem_uid)
            )
        except KeyError:
            print(f"{ecosystem_uid}'s management {management} cannot be turned "
                  f"to {status} yet")

    def on_get_data_since(self, message: dict) -> None:
        if not self.db:
            logger.error(
                "Received 'get_data_since' event but USE_DATABASE is set to False"
            )
            return
        ecosystem_uids: str = message["ecosystems"]
        uids: list = self._get_uid_list(ecosystem_uids)
        since_str: str = message["since"]
        since: datetime = datetime.fromisoformat(since_str).astimezone()
        with self.db.scopped_session() as session:
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
