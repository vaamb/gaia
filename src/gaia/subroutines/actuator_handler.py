from __future__ import annotations

import time
import typing as t
from typing import Callable
import weakref

from gaia_validators import (
    ActuatorMode, ActuatorModePayload, HardwareType)


if t.TYPE_CHECKING:
    from gaia.subroutines.template import SubroutineTemplate


def allways_off(**kwargs) -> bool:
    return False


class ActuatorHandler:
    def __init__(
            self,
            subroutine: "SubroutineTemplate",
            actuator_type: HardwareType,
            expected_status_function: Callable[..., bool] = allways_off
    ) -> None:
        self.subroutine: "SubroutineTemplate" = weakref.proxy(subroutine)
        assert actuator_type != HardwareType.sensor
        self.type = actuator_type
        self._timer_on: bool = False
        self._time_limit: float = 0.0
        self._expected_status_function: Callable[..., bool] = expected_status_function

    @property
    def active(self) -> bool:
        return self.subroutine.ecosystem._actuators_state[self.type.value]["active"]

    @active.setter
    def active(self, value: bool) -> None:
        self.subroutine.ecosystem._actuators_state[self.type.value]["active"] = value

    @property
    def mode(self) -> ActuatorMode:
        return self.subroutine.ecosystem._actuators_state[self.type.value]["mode"]

    @mode.setter
    def mode(self, value: ActuatorMode) -> None:
        self.subroutine.ecosystem._actuators_state[self.type.value]["mode"] = value

    @property
    def status(self) -> bool:
        return self.subroutine.ecosystem._actuators_state[self.type.value]["status"]

    @status.setter
    def status(self, value: bool) -> None:
        self.subroutine.ecosystem._actuators_state[self.type.value]["status"] = value

    @property
    def countdown(self) -> float | None:
        if self._timer_on:
            countdown = self._time_limit - time.monotonic()
            if countdown > 0.0:
                return countdown
            return 0.0
        return None

    def reset_countdown(self) -> None:
        self._timer_on = False
        self._time_limit = 0.0

    def increase_countdown(self, delta_time: float) -> None:
        if self._time_limit:
            self.subroutine.logger.info(f"Increasing timer by {delta_time} seconds")
            self._time_limit += delta_time
        else:
            self._time_limit = time.monotonic() + delta_time
        self._timer_on = True

    def decrease_countdown(self, delta_time: float) -> None:
        if self._time_limit:
            self.subroutine.logger.info(f"Decreasing timer by {delta_time} seconds")
            self._time_limit -= delta_time
            if self._time_limit <= 0:
                self._time_limit = 0.0
        else:
            raise AttributeError("No timer set, you cannot reduce the countdown")

    def turn_to(
            self,
            turn_to: ActuatorModePayload = ActuatorModePayload.automatic,
            countdown: float = 0.0
    ):
        if turn_to == ActuatorModePayload.automatic:
            self.mode = ActuatorMode.automatic
        else:
            self.mode = ActuatorMode.manual
            if turn_to == ActuatorModePayload.on:
                self.status = True
            else:  # turn_to == ActuatorModePayload.off
                self.status = False
        additional_message = ""
        if countdown:
            self._time_limit = 0.0
            self.increase_countdown(countdown)
            additional_message = f" for {countdown} seconds"
        self.subroutine.logger.info(
            f"{self.type.value} have been manually turned to '{turn_to.value}'"
            f"{additional_message}")

    def compute_expected_status(self, **kwargs) -> bool:
        countdown = self.countdown
        if countdown is not None and countdown <= 0.1:
            self.mode = ActuatorMode.automatic
            self.reset_countdown()
        if self.mode == ActuatorMode.automatic:
            return self._expected_status_function(**kwargs)
        else:
            if self.status:
                return True
            return False
