from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import typing as t
from time import monotonic
from typing import Generic, Type, TypeVar


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.config.from_files import EcosystemConfig
    from gaia.ecosystem import Ecosystem


HARDWARE_TYPE = TypeVar("HARDWARE_TYPE")


class SubroutineTemplate(ABC, Generic[HARDWARE_TYPE]):
    def __init__(self, ecosystem: Ecosystem) -> None:
        """Base class to manage an ecosystem subroutine"""
        self._ecosystem: Ecosystem = ecosystem
        self.name: str = self.__class__.__name__.lower()
        eco_name = self._ecosystem.name.replace(" ", "_")
        self.logger: logging.Logger = logging.getLogger(
            f"gaia.engine.{eco_name}.{self.name}")
        self.logger.debug("Initializing ...")
        self._hardware_choices: dict[str, Type[Type[HARDWARE_TYPE]]] = {}
        self._started: bool = False

    def _finish__init__(self) -> None:
        if not self._hardware_choices:
            raise ValueError("No hardware choices specified.")
        self.logger.debug("Initialization successfully.")

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.__class__.__name__}({self.ecosystem.uid}, status={self.started})"

    @abstractmethod
    async def _routine(self) -> None:
        raise NotImplementedError("This method must be implemented in a subclass")

    @abstractmethod
    def _compute_if_manageable(self) -> bool:
        raise NotImplementedError("This method must be implemented in a subclass")

    @abstractmethod
    async def _start(self) -> None:
        raise NotImplementedError("This method must be implemented in a subclass")

    @abstractmethod
    async def _stop(self) -> None:
        raise NotImplementedError("This method must be implemented in a subclass")

    """API calls"""
    @property
    def ecosystem(self) -> Ecosystem:
        return self._ecosystem

    @property
    def config(self) -> EcosystemConfig:
        return self._ecosystem.config

    @property
    def started(self) -> bool:
        return self._started

    @property
    def enabled(self) -> bool:
        return self.config.get_management(self.name)

    def enable(self) -> None:
        self.logger.info("Enabling the subroutine.")
        self.config.set_management(self.name, True)

    def disable(self) -> None:
        self.logger.info("Disabling the subroutine.")
        self.config.set_management(self.name, False)

    @property
    def manageable(self) -> bool:
        return self._compute_if_manageable()

    @property
    def hardware_choices(self) -> dict[str, Type[HARDWARE_TYPE]]:
        return self._hardware_choices

    @hardware_choices.setter
    def hardware_choices(self, choices: dict[str, Type[HARDWARE_TYPE]]) -> None:
        self._hardware_choices = choices

    async def routine(self) -> None:
        name = self.__class__.__name__
        start = monotonic()
        if not self.started:
            raise RuntimeError(
                f"{name} subroutine has to be started to use its 'routine' method")
        self.logger.debug(f"Starting {name} routine ...")
        await self._routine()
        routine_time = monotonic() - start
        self.logger.debug(f"{name} routine finished in {routine_time:.1f} s.")

    @abstractmethod
    def get_hardware_needed_uid(self) -> set[str]:
        raise NotImplementedError("This method must be implemented in a subclass.")

    @property
    def hardware(self) -> dict[str, HARDWARE_TYPE]:
        return {
            uid: self.ecosystem.hardware[uid]
            for uid in self.get_hardware_needed_uid()
        }

    async def refresh(self) -> None:
        assert all([
            hardware.model in self.hardware_choices
            for hardware in self.hardware.values()
        ])
        # Make sure the routine is still manageable
        if not self._compute_if_manageable():
            self.logger.warning(
                f"The {self.name.capitalize()} subroutine is not manageable and "
                f"will stop.")
            await self.stop()
            return

    async def start(self) -> None:
        if self.started:
            raise RuntimeError("The subroutine is already running.")
        if not self.enabled:
            raise RuntimeError("The subroutine is not enabled.")
        if not self.manageable:
            raise RuntimeError("The subroutine is not manageable.")
        self.logger.debug("Starting the subroutine.")
        try:
            await self.refresh()
            await self._start()
            self.logger.debug("Successfully started.")
            self._started = True
        except Exception as e:
            self._started = False
            self.logger.error(
                f"Starting failed. ERROR msg: `{e.__class__.__name__}: {e}`.")
            raise e

    async def stop(self) -> None:
        if not self.started:
            raise RuntimeError("The subroutine is not running.")
        self.logger.debug("Stopping the subroutine.")
        try:
            await self._stop()
            self._started = False
            self.logger.debug("Successfully stopped.")
        except Exception as e:
            self._started = True
            self.logger.error(
                f"Stopping failed. ERROR msg: `{e.__class__.__name__}: {e}`."
            )
            raise e
