from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import typing as t
from typing import Type
import weakref

from gaia_validators import HardwareConfig

from gaia.exceptions import HardwareNotFound
from gaia.hardware.abc import BaseSensor, Camera, Dimmer, Hardware, Switch


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.config.from_files import EcosystemConfig
    from gaia.ecosystem import Ecosystem


class SubroutineTemplate(ABC):
    def __init__(self, ecosystem: "Ecosystem") -> None:
        """Base class to manage an ecosystem subroutine
        """
        self._ecosystem: "Ecosystem" = weakref.proxy(ecosystem)
        self._uid: str = self._ecosystem.uid
        self._ecosystem_name: str = self._ecosystem.name
        self.name: str = self.__class__.__name__.lower()
        self.logger: logging.Logger = logging.getLogger(
            f"gaia.engine.{self._ecosystem_name}.{self.name}"
        )
        self.logger.debug("Initializing")
        self.hardware: dict[str, Hardware] = {}
        self.manageable: bool = True
        self._started: bool = False

    def _finish__init__(self) -> None:
        self.logger.debug("Initialization successfully")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.name}, status={self.status})"

    def _add_hardware(
            self,
            hardware_config: HardwareConfig,
            hardware_choice: dict[str, Type[Hardware]],
    ) -> BaseSensor | Camera | Dimmer | Hardware | Switch | None:
        try:
            model: str = hardware_config.model
            if model not in hardware_choice:
                raise HardwareNotFound(
                    f"{model} is not in the list of the hardware available."
                )
            hardware_class: Type[Hardware] = hardware_choice[model]
            hardware = hardware_class.from_hardware_config(hardware_config, self)
            if isinstance(hardware, Switch):
                hardware.turn_off()
            if isinstance(hardware, Dimmer):
                hardware.set_pwm_level(0)
            self.logger.debug(f"Hardware {hardware.name} has been set up")
            self.hardware[hardware.uid] = hardware
            return hardware
        except Exception as e:
            uid = hardware_config.uid
            self.logger.error(
                f"Encountered an exception while setting up hardware '{uid}'. "
                f"ERROR msg: `{e.__class__.__name__}: {e}`."
            )

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
    def config(self) -> "EcosystemConfig":
        return self._ecosystem.config

    @property
    def ecosystem_uid(self) -> str:
        return self._ecosystem.uid

    @property
    def status(self) -> bool:
        return self._started

    @property
    def management(self) -> bool:
        return self.config.get_management(self.name)

    @management.setter
    def management(self, value: bool) -> None:  # TODO: save changes
        self.config.set_management(self.name, value)

    @abstractmethod
    def add_hardware(self, hardware_config: HardwareConfig) -> None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    def remove_hardware(self, hardware_uid: str) -> None:
        try:
            del self.hardware[hardware_uid]
        except KeyError:
            self.logger.error(f"Hardware '{hardware_uid}' does not exist")

    @abstractmethod
    def get_hardware_needed_uid(self) -> set[str]:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    def refresh_hardware(self) -> None:
        hardware_needed: set[str] = self.get_hardware_needed_uid()
        hardware_existing: set[str] = set(self.hardware)
        for hardware_uid in hardware_needed - hardware_existing:
            hardware_config = self.config.get_hardware_config(hardware_uid)
            self.add_hardware(hardware_config)
        for hardware_uid in hardware_existing - hardware_needed:
            self.remove_hardware(hardware_uid)

    def update_manageable(self) -> None:
        if self.management:
            self._update_manageable()

    def start(self) -> None:
        self.update_manageable()
        if self.manageable:
            if not self._started:
                self.logger.debug("Starting the subroutine")
                try:
                    self.refresh_hardware()
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
                for hardware_uid in [*self.hardware.keys()]:
                    self.remove_hardware(hardware_uid)
                self._started = False
                self.logger.debug("Successfully stopped")
            except Exception as e:
                self._started = True
                self.logger.error(
                    f"Stopping failed. ERROR msg: `{e.__class__.__name__}: {e}`."
                )
                raise e
