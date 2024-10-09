from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import typing as t
from typing import Type

import gaia_validators as gv

from gaia.exceptions import HardwareNotFound
from gaia.hardware.abc import BaseSensor, Camera, Dimmer, Hardware, Switch


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.config.from_files import EcosystemConfig
    from gaia.ecosystem import Ecosystem


class SubroutineTemplate(ABC):
    def __init__(self, ecosystem: "Ecosystem") -> None:
        """Base class to manage an ecosystem subroutine
        """
        self._ecosystem: "Ecosystem" = ecosystem
        self.name: str = self.__class__.__name__.lower()
        eco_name = self._ecosystem.name.replace(" ", "_")
        self.logger: logging.Logger = logging.getLogger(
            f"gaia.engine.{eco_name}.{self.name}")
        self.logger.debug("Initializing ...")
        self.hardware: dict[str, Hardware] = {}
        self._hardware_choices: dict[str, Type[Hardware]] = {}
        self._started: bool = False

    def _finish__init__(self) -> None:
        self.logger.debug("Initialization successfully.")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.ecosystem.uid}, status={self.started})"

    @abstractmethod
    async def routine(self) -> None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    @abstractmethod
    def _compute_if_manageable(self) -> bool:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    @abstractmethod
    async def _start(self) -> None:
        raise NotImplementedError(
            "This method must be implemented in a subclass"
        )

    @abstractmethod
    async def _stop(self) -> None:
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
    def started(self) -> bool:
        return self._started

    @property
    def enabled(self) -> bool:
        return self.config.get_management(self.name)

    def enable(self) -> None:
        self.logger.info(f"Enabling the subroutine.")
        self.config.set_management(self.name, True)

    def disable(self) -> None:
        self.logger.info(f"Disabling the subroutine.")
        self.config.set_management(self.name, False)

    @property
    def manageable(self) -> bool:
        return self._compute_if_manageable()

    @property
    def hardware_choices(self) -> dict[str, Type[Hardware]]:
        return self._hardware_choices

    @hardware_choices.setter
    def hardware_choices(self, choices: dict[str, Type[Hardware]]) -> None:
        self._hardware_choices = choices

    async def add_hardware(
            self,
            hardware_config: gv.HardwareConfig,
    ) -> BaseSensor | Camera | Dimmer | Hardware | Switch | None:
        if not self.hardware_choices:
            raise RuntimeError("No 'hardware_choices' available.")
        try:
            model: str = hardware_config.model
            if model not in self.hardware_choices:
                raise HardwareNotFound(
                    f"{model} is not in the list of the hardware available."
                )
            hardware_class: Type[Hardware] = self.hardware_choices[model]
            hardware = hardware_class.from_hardware_config(hardware_config, self)
            if isinstance(hardware, Switch):
                await hardware.turn_off()
            if isinstance(hardware, Dimmer):
                await hardware.set_pwm_level(0)
            self.logger.debug(f"Hardware {hardware.name} has been set up.")
            self.hardware[hardware.uid] = hardware
            return hardware
        except Exception as e:
            uid = hardware_config.uid
            self.logger.error(
                f"Encountered an exception while setting up hardware '{uid}'. "
                f"ERROR msg: `{e.__class__.__name__}: {e}`."
            )

    async def remove_hardware(self, hardware_uid: str) -> None:
        if not self.hardware.get(hardware_uid):
            error_msg =f"Hardware '{hardware_uid}' not found."
            self.logger.error(error_msg)
            raise HardwareNotFound(error_msg)

        hardware = self.hardware[hardware_uid]
        if isinstance(hardware, Switch):
            await hardware.turn_off()
        if isinstance(hardware, Dimmer):
            await hardware.set_pwm_level(0)
        del self.hardware[hardware_uid]
        self.logger.debug(f"Hardware {hardware.name} has been dismounted.")

    @abstractmethod
    def get_hardware_needed_uid(self) -> set[str]:
        raise NotImplementedError(
            "This method must be implemented in a subclass."
        )

    async def refresh_hardware(self) -> None:
        hardware_needed: set[str] = self.get_hardware_needed_uid()
        hardware_existing: set[str] = set(self.hardware)
        for hardware_uid in hardware_needed - hardware_existing:
            hardware_config = self.config.get_hardware_config(hardware_uid)
            await self.add_hardware(hardware_config)
        for hardware_uid in hardware_existing - hardware_needed:
            await self.remove_hardware(hardware_uid)

    async def start(self) -> None:
        if self.started:
            raise RuntimeError("The subroutine is already running.")
        if not self.enabled:
            raise RuntimeError("The subroutine is not enabled.")
        if not self.manageable:
            raise RuntimeError("The subroutine is not manageable.")
        self.logger.debug("Starting the subroutine.")
        try:
            await self.refresh_hardware()
            await self._start()
            self.logger.debug("Successfully started.")
            self._started = True
        except Exception as e:
            self._started = False
            self.logger.error(
                f"Starting failed. "
                f"ERROR msg: `{e.__class__.__name__}: {e}`."
            )
            raise e

    async def stop(self) -> None:
        if not self.started:
            raise RuntimeError("The subroutine is not running.")
        self.logger.debug(f"Stopping the subroutine.")
        try:
            await self._stop()
            for hardware_uid in [*self.hardware.keys()]:
                await self.remove_hardware(hardware_uid)
            self.hardware = {}
            self._started = False
            self.logger.debug("Successfully stopped.")
        except Exception as e:
            self._started = True
            self.logger.error(
                f"Stopping failed. ERROR msg: `{e.__class__.__name__}: {e}`."
            )
            raise e
