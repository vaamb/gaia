import logging
import logging.config
import typing as t
import weakref

from .config_parser import get_config, SpecificConfig
from .exceptions import NoSubroutineNeeded, UndefinedParameter
from .subroutines.chaos import Chaos
from .subroutines import SUBROUTINES
from .subroutines.template import SubroutineTemplate
from config import Config


if t.TYPE_CHECKING:  # pragma: no cover
    from .engine import Engine
    from .events import Events
    from .subroutines import Health, Light, Sensors


class Ecosystem:
    """An Ecosystem class that manages subroutines

    The Ecosystem will take care of creating, starting and stopping the required
    subroutines that will themselves act on the physical ecosystem parameters

    : param ecosystem_id: The name or the uid of an ecosystem, as written in
                          'ecosystems.cfg'
    """
    def __init__(self, ecosystem_id, engine: "Engine"):
        self._config: SpecificConfig = get_config(ecosystem_id)
        self._uid: str = self._config.uid
        self._name: str = self._config.name
        self._engine: "Engine" = weakref.proxy(engine)
        self.logger: logging.Logger = logging.getLogger(
            f"{Config.APP_NAME.lower()}.engine.{self._name}"
        )
        self.logger.info("Initializing Ecosystem")
        self._alarms: list = []
        self.subroutines: SUBROUTINES = {}
        for subroutine in SUBROUTINES:
            self.init_subroutine(subroutine)
        self._chaos: Chaos = Chaos(self, 0, 0, 1)
        self.refresh_chaos()
        self._started: bool = False
        self.logger.debug(f"Ecosystem initialization successful")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.uid}, name={self.name}, " \
               f"status={self.status}, engine={self._engine})"

    def _refresh_subroutines(self) -> None:
        # Need to start sensors and lights before other subroutines
        subroutines_ordered = set(SUBROUTINES.keys())
        subroutines_needed = subroutines_ordered.intersection(
            self._config.get_managed_subroutines()
        )
        to_stop = self.subroutines_started - subroutines_needed
        for subroutine in to_stop:
            self.stop_subroutine(subroutine)
        if not subroutines_needed:
            raise NoSubroutineNeeded
        for subroutine in self.subroutines_started:
            self.subroutines[subroutine].refresh_hardware()
        to_start = subroutines_needed - self.subroutines_started
        for subroutine in to_start:
            self.start_subroutine(subroutine)

    def refresh_chaos(self):
        try:
            values = self.config.chaos
        except UndefinedParameter:
            values = {}
        finally:
            self.chaos.frequency = values.get("frequency", 0)
            self.chaos.duration = values.get("duration", 0)
            self.chaos.intensity = values.get("intensity", 1)
            self.chaos.update()

    """
    API calls
    """
    def init_subroutine(self, subroutine_name: str) -> None:
        """Initialize a Subroutines

        :param subroutine_name: The name of the Subroutines to initialize
        """
        self.subroutines[subroutine_name] = SUBROUTINES[subroutine_name](self)

    def start_subroutine(self, subroutine_name: str) -> None:
        """Start a Subroutines

        :param subroutine_name: The name of the Subroutines to start
        """
        self.subroutines[subroutine_name].start()

    def stop_subroutine(self, subroutine_name: str) -> None:
        """Stop a Subroutines

        :param subroutine_name: The name of the Subroutines to stop
        """
        self.subroutines[subroutine_name].stop()

    def refresh_subroutines(self) -> None:
        """Start and stop the Subroutines based on the 'ecosystem.cfg' file"""
        for subroutine in self.subroutines.values():
            subroutine.update_manageable()
        try:
            self._refresh_subroutines()
        except NoSubroutineNeeded:
            if self.status:
                self.logger.info("No subroutine are running, stopping the Ecosystem")
                self.stop()

    def start(self):
        """Start the Ecosystem

        When started, the Ecosystem will automatically start and stop the
        Subroutines based on the 'ecosystem.cfg' file
        """
        if not self.status:
            try:
                self.logger.info("Starting the Ecosystem")
                self._refresh_subroutines()
                self.logger.debug(f"Ecosystem successfully started")
                self._started = True
            except NoSubroutineNeeded:
                self.logger.info(
                    "The Ecosystem isn't managing any subroutine, it will stop"
                )
        else:
            raise RuntimeError(f"Ecosystem {self._name} is already running")

    def stop(self):
        """Stop the Ecosystem"""
        if self.status:
            self.logger.info("Stopping the Ecosystem ...")
            for subroutine in reversed(list(SUBROUTINES.keys())):
                self.subroutines[subroutine].stop()
            if not any([self.subroutines[subroutine].status
                        for subroutine in self.subroutines]):
                self.logger.debug("Ecosystem successfully stopped")
            else:
                self.logger.error("Failed to stop Ecosystem")
                raise Exception(f"Failed to stop Ecosystem {self._name}")
            self._started = False

    # General info
    @property
    def uid(self) -> str:
        return self._uid

    @property
    def name(self) -> str:
        return self._name

    @property
    def status(self) -> bool:
        return self._started

    @property
    def config(self) -> SpecificConfig:
        return self._config

    @property
    def engine(self) -> "Engine":
        return self._engine

    @property
    def event_handler(self) -> "Events":
        return self._engine.event_handler

    @property
    def chaos(self) -> Chaos:
        return self._chaos

    @property
    def subroutines_started(self) -> set:
        return set([
            subroutine for subroutine in self.subroutines
            if self.subroutines[subroutine].status
        ])

    @property
    def base_info(self) -> dict:
        return {
            "uid": self.uid,
            "name": self.name,
            "status": self.status,
        }

    @property
    def management(self) -> dict:
        """Return the subroutines' management corrected by whether they are
        manageable or not"""
        base_management = self.config.ecosystem_config["management"]
        management = {}
        for m in base_management:
            try:
                management[m] = self.config.get_management(m) & self.subroutines[m].manageable
            except KeyError:
                management[m] = self.config.get_management(m)
        return management

    @property
    def environmental_parameters(self) -> dict:
        return self.config.ecosystem_config.get("environment", {})

    @property
    def hardware(self) -> dict:
        return self.config.ecosystem_config.get("IO", {})

    # Actuator
    def turn_actuator(
            self,
            actuator: str,
            mode: str = "automatic",
            countdown: float = 0.0
    ) -> None:
        """Turn the actuator to the specified mode

        :param actuator: the name of a type of actuators, ex: 'lights'.
        :param mode: the mode to which the actuator needs to be set. Can be
                     'on', 'off' or 'automatic'.
        :param countdown: the delay before which the actuator will be turned to
                          the specified mode.
        """
        try:
            if actuator.lower() == "light":
                light_subroutine: "Light" = self.subroutines["light"]
                light_subroutine.turn_light(
                    mode=mode, countdown=countdown
                )
        except RuntimeError:
            self.logger.error(
                f"Cannot turn {actuator} to {mode} as the subroutine managing it "
                f"is not currently running"
            )

    # Light
    @property
    def light_info(self) -> dict:
        light_subroutine: "Light" = self.subroutines["light"]
        if light_subroutine.status:
            return light_subroutine.light_info
        return {}

    def update_sun_times(self, send=False) -> None:
        self.logger.debug("Updating sun times")
        light_subroutine: "Light" = self.subroutines["light"]
        if light_subroutine.status:
            light_subroutine.update_sun_times(send=send)
        else:
            self.logger.warning(
                f"Cannot update sun times as the light subroutine is not "
                f"currently running"
            )

    # Sensors
    @property
    def sensors_data(self) -> dict:
        sensors_subroutine: "Sensors" = self.subroutines["sensors"]
        if sensors_subroutine.status:
            return sensors_subroutine.sensors_data
        return {}

    # Health
    @property
    def plants_health(self) -> dict:
        health_subroutine: "Health" = self.subroutines["health"]
        if health_subroutine.status:
            return health_subroutine.plants_health
        return {}
