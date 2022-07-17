from abc import ABC, abstractmethod
import logging
import typing as t
import weakref

from ..exceptions import HardwareNotFound
from ..hardware.ABC import BaseSensor, Dimmer, Hardware, Switch
from config import Config


if t.TYPE_CHECKING:  # pragma: no cover
    from src.config_parser import SpecificConfig
    from src.ecosystem import Ecosystem


class SubroutineTemplate(ABC):
    def __init__(self, ecosystem: "Ecosystem") -> None:
        """Base class to manage an ecosystem subroutine
        """
        self._ecosystem: "Ecosystem" = weakref.proxy(ecosystem)
        self._uid: str = self._ecosystem.uid
        self._ecosystem_name: str = self._ecosystem.name
        self.name: str = self.__class__.__name__.lower()
        self.logger: logging.Logger = logging.getLogger(
            f"{Config.APP_NAME.lower()}.engine.{self._ecosystem_name}.{self.name}"
        )
        self.logger.debug("Initializing")
        self.hardware: dict[str, Hardware] = {}
        self.manageable: bool = True
        self.update_manageable()
        self._started: bool = False

    def _finish__init__(self) -> None:
        self.logger.debug("Initialization successfully")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.name}, status={self.status})"

    def _add_hardware(
            self,
            hardware_dict: dict[str, dict[str, str]],
            hardware_choice: dict[str, t.Type[Hardware]],
    ) -> t.Union[BaseSensor, Dimmer, Hardware, Switch]:
        hardware_uid: str = list(hardware_dict.keys())[0]
        hardware_info: dict = hardware_dict[hardware_uid]
        model: str = hardware_info.get("model", None)
        if model not in hardware_choice:
            raise HardwareNotFound(
                f"{model} is not in the list of the hardware available."
            )
        hardware_class: t.Type[Hardware] = hardware_choice[model]
        hardware = hardware_class(
            subroutine=self,
            uid=hardware_uid,
            **hardware_info
        )
        return hardware

    def _refresh_hardware(self, hardware_group: str) -> None:
        hardware_needed: t.Set[str] = set(self.config.get_IO_group(hardware_group))
        hardware_existing: t.Set[str] = set(self.hardware)
        for hardware_uid in hardware_needed - hardware_existing:
            self.add_hardware({hardware_uid: self.config.IO_dict[hardware_uid]})
        for hardware_uid in hardware_existing - hardware_needed:
            self.remove_hardware(hardware_uid)

    @abstractmethod
    def _update_manageable(self) -> None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    @abstractmethod
    def _start(self) -> None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    @abstractmethod
    def _stop(self) -> None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    """API calls"""
    @property
    def ecosystem(self) -> "Ecosystem":
        return self._ecosystem

    @property
    def config(self) -> "SpecificConfig":
        return self._ecosystem.config

    @property
    def status(self) -> bool:
        return self._started

    @abstractmethod
    def add_hardware(self, hardware_dict: dict) -> t.Union[BaseSensor, Dimmer, Hardware, Switch]:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    @abstractmethod
    def remove_hardware(self, hardware_uid: str) -> None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    @abstractmethod
    def refresh_hardware(self) -> None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    def update_manageable(self) -> None:
        if self.config.get_management(self.name) and not Config.TESTING:
            self._update_manageable()

    def set_management(self, value):
        self.config.set_management(self.name, value)

    def start(self) -> None:
        if self.manageable:
            if not self._started:
                self.logger.debug("Starting the subroutine")
                try:
                    self._start()
                    self.logger.debug("Successfully started")
                    self._started = True
                except Exception as e:
                    self._started = False
                    self.logger.error(
                        f"Starting failed. "
                        f"ERROR msg: `{e.__class__.__name__}: {e}`."
                    )
                    raise e
            else:
                raise RuntimeError("Subroutine is already running")
        else:
            self.logger.error(
                "The subroutine has been disabled and cannot be started"
            )

    def stop(self) -> None:
        if self._started:
            self.logger.debug(f"Stopping the subroutine")
            try:
                self._stop()
                self._started = False
                self.logger.debug("Successfully stopped")
            except Exception as e:
                self._started = True
                self.logger.error(
                    f"Stopping failed. ERROR msg: `{e.__class__.__name__}: {e}`."
                )
                raise e
