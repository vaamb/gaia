import logging
import typing as t

from src.utils import encrypted_uid, generate_uid_token
from config import Config


if t.TYPE_CHECKING:  # pragma: no cover
    from src.ecosystem import Ecosystem


logger = logging.getLogger(f"{Config.APP_NAME.lower()}.broker")


class Events:
    """A class holding all the events coming from either socketio or
    event-dispatcher

    : param ecosystem_dict: a dict holding all the Ecosystem instances
    """
    def __init__(self, ecosystem_dict: dict[str, "Ecosystem"]) -> None:
        self.ecosystems = ecosystem_dict
        self._registered = False

    def emit(self, *args, **kwargs):
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    def on_connect(self) -> None:
        logger.info(
            "Connection successful. Trying to register the engine"
        )
        self.on_register()

    def on_disconnect(self) -> None:
        if self._registered:
            logger.warning("Disconnected from server")
        else:
            logger.error("Failed to register engine")

    def on_register(self) -> None:
        self.emit("register_engine",
                  data={
                      "ikys": encrypted_uid(),
                      "uid_token": generate_uid_token(),
                  })

    def on_register_ack(self) -> None:
        logger.info("Engine registration successful")
        self._registered = True

    def on_ping(self) -> None:
        logger.debug("Received ping event")
        pong = []
        for ecosystem in self.ecosystems.values():
            pong.append(ecosystem.uid)
        self.emit("pong", data=pong)

    def _get_uid_list(self, ecosystem_uids: t.Union[str, tuple] = "all") -> list:
        if "all" in ecosystem_uids:
            return [e_uid for e_uid in self.ecosystems.keys()]
        if isinstance(ecosystem_uids, str):
            return [ecosystem_uids] \
                if ecosystem_uids in self.ecosystems.keys() else []
        else:
            return [e_uid for e_uid in ecosystem_uids
                    if e_uid in self.ecosystems.keys()]

    def on_send_config(self, ecosystem_uids: t.Union[str, tuple] = "all") -> None:
        logger.debug("Received send_config event")
        uids = self._get_uid_list(ecosystem_uids)
        [self._send_config(config_type, uids) for config_type in
         ("base_info", "management", "environmental_parameters", "hardware")]

    def _send_config(
            self,
            config_type: str,
            ecosystem_uids: t.Union[str, tuple, list] = "all"
    ) -> None:
        uids = self._get_uid_list(ecosystem_uids)
        rv = []
        for uid in uids:
            data = getattr(self.ecosystems[uid], config_type)
            if data:
                data.reload({"uid": uid})
                rv.append(data)
        self.emit(config_type, rv)

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

    def on_send_sensors_data(self, ecosystem_uids: t.Union[str, tuple] = "all") -> None:
        logger.debug("Received send_sensors_data event")
        self.emit(
            "sensors_data",
            self._get_data("sensors_data", ecosystem_uids=ecosystem_uids)
        )

    def on_send_health_data(self, ecosystem_uids: t.Union[str, tuple] = "all") -> None:
        logger.debug("Received send_health_data event")
        self.emit(
            "health_data",
            self._get_data("plants_health", ecosystem_uids=ecosystem_uids)
        )

    def on_send_light_data(self, ecosystem_uids: t.Union[str, tuple] = "all") -> None:
        logger.debug("Received send_light_data event")
        self.emit("light_data",
                  self._get_data("light_info", ecosystem_uids=ecosystem_uids))

    def on_turn_light(self, message: dict) -> None:
        logger.debug("Received turn_light event")
        ecosystem_uid = message["ecosystem"]
        mode = message["mode"]
        countdown = message.get("countdown", 0)
        try:
            self.ecosystems[ecosystem_uid].turn_actuator(
                "light", mode=mode, countdown=countdown
            )
            self.on_send_light_data(ecosystem_uid)
        # Except when subroutines are still loading
        except KeyError:
            print(f"{ecosystem_uid}'s light subroutine has not initialized yet")

    def on_turn_actuator(self, message: dict) -> None:
        logger.debug("Received turn_actuator event")
        ecosystem_uid = message["ecosystem"]
        actuator = message["actuator"]
        mode = message["mode"]
        countdown = message.get("countdown", 0)
        try:
            self.ecosystems[ecosystem_uid].turn_actuator(
                actuator=actuator, mode=mode, countdown=countdown
            )
        # Except when subroutines are still loading
        except KeyError:
            print(f"{ecosystem_uid}'s {actuator} cannot be turned to {mode} yet")
        finally:
            if actuator == "light":
                self.on_send_light_data(ecosystem_uid)

    def on_change_management(self, message: dict) -> None:
        ecosystem_uid = message["ecosystem"]
        management = message["management"]
        status = message["status"]
        try:
            self.ecosystems[ecosystem_uid].config.set_management(management, status)
            self.ecosystems[ecosystem_uid].config.save()
            self._send_config("management")
        except KeyError:
            print(f"{ecosystem_uid}'s management {management} cannot be turned "
                  f"to {status} yet")
