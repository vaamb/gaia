from __future__ import annotations

import logging
import time
import typing as t
from typing import Callable
import weakref

import gaia_validators as gv

from gaia.hardware.abc import Dimmer, Hardware, Switch
from gaia.subroutines import Climate, Light


if t.TYPE_CHECKING:
    from gaia import Ecosystem


def always_off(**kwargs) -> bool:
    return False


class ActuatorHandler:
    __slots__ = (
        "_active", "_expected_status_function", "_level", "_last_mode",
        "_last_status", "_mode", "_status", "_time_limit", "_timer_on",
        "ecosystem", "logger", "type"
    )

    def __init__(
            self,
            ecosystem: Ecosystem,
            actuator_type: gv.HardwareType,
            expected_status_function: Callable[..., bool] = always_off
    ) -> None:
        self.ecosystem: Ecosystem = ecosystem
        assert actuator_type != gv.HardwareType.sensor
        self.type = actuator_type
        eco_name = self.ecosystem.name.replace(" ", "_")
        self.logger = logging.getLogger(
            f"gaia.engine.{eco_name}.actuators.{self.type.name}")
        self._active: int = 0
        self._status: bool = False
        self._level: float | None = None
        self._mode: gv.ActuatorMode = gv.ActuatorMode.automatic
        self._timer_on: bool = False
        self._time_limit: float = 0.0
        self._expected_status_function: Callable[..., bool] = \
            expected_status_function
        self._last_status: bool = self.status
        self._last_mode: gv.ActuatorMode = self.mode

    # TODO: maybe cache
    def get_actuators_handled(self) -> list[Switch | Dimmer]:
        return [
            hardware for hardware in Hardware.get_mounted().values()
            if hardware.ecosystem_uid == self.ecosystem.uid
            and hardware.type == self.type
        ]

    def as_dict(self) -> gv.ActuatorStateDict:
        return {
            "active": self.active,
            "status": self._status,
            "level": self._level,
            "mode": self._mode,
        }

    @property
    def active(self) -> bool:
        return self._active > 0

    def activate(self) -> None:
        self._active += 1

    def deactivate(self) -> None:
        self._active -= 1

    @property
    def mode(self) -> gv.ActuatorMode:
        return self._mode

    def set_mode(self, value: gv.ActuatorMode) -> None:
        self._set_mode_no_update(value)
        if self._mode != self._last_mode:
            self.logger.info(
                f"{self.type.value.capitalize()} has been set to "
                f"'{self.mode.value}' mode")
            self.send_actuators_state()
            self._last_mode = self._mode

    def _set_mode_no_update(self, value: gv.ActuatorMode) -> None:
        validated_value = gv.safe_enum_from_name(gv.ActuatorMode, value)
        self._mode = validated_value

    @property
    def last_mode(self) -> gv.ActuatorMode:
        return self._last_mode

    @property
    def status(self) -> bool:
        return self._status

    def set_status(self, value: bool) -> None:
        self._set_status_no_update(value)
        if self._status != self._last_status:
            self.logger.info(
                f"{self.type.value.capitalize()} has been turned "
                f"{'on' if self.status else 'off'}")
            self.send_actuators_state()
            self._last_status = self._status

    def turn_on(self) -> None:
        self.set_status(True)

    def turn_off(self) -> None:
        self.set_status(False)

    def _set_status_no_update(self, value: bool) -> None:
        self._status = value
        for actuator in self.get_actuators_handled():
            if isinstance(actuator, Switch):
                if value:
                    actuator.turn_on()
                else:
                    actuator.turn_off()

    @property
    def last_status(self) -> bool:
        return self._last_status

    @property
    def level(self) -> float | None:
        return self._level

    def set_level(self, pwm_level: float) -> None:
        self._level = pwm_level
        for actuator in self.get_actuators_handled():
            if isinstance(actuator, Dimmer):
                actuator.set_pwm_level(pwm_level)

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
            self.logger.info(f"Increasing timer by {delta_time} seconds")
            self._time_limit += delta_time
        else:
            self._time_limit = time.monotonic() + delta_time
        self._timer_on = True

    def decrease_countdown(self, delta_time: float) -> None:
        if self._time_limit:
            self.logger.info(f"Decreasing timer by {delta_time} seconds")
            self._time_limit -= delta_time
            if self._time_limit <= 0:
                self._time_limit = 0.0
        else:
            raise AttributeError("No timer set, you cannot reduce the countdown")

    def turn_to(
            self,
            turn_to: gv.ActuatorModePayload = gv.ActuatorModePayload.automatic,
            countdown: float = 0.0
    ) -> None:
        turn_to: gv.ActuatorModePayload = gv.safe_enum_from_name(
            gv.ActuatorModePayload, turn_to)
        additional_message = ""
        if turn_to == gv.ActuatorModePayload.automatic:
            self._set_mode_no_update(gv.ActuatorMode.automatic)
        else:
            self._set_mode_no_update(gv.ActuatorMode.manual)
            if turn_to == gv.ActuatorModePayload.on:
                self._set_status_no_update(True)
            else:  # turn_to == ActuatorModePayload.off
                self._set_status_no_update(False)
            if countdown:
                self._time_limit = 0.0
                self.increase_countdown(countdown)
                additional_message = f" for {countdown} seconds"
        self.logger.info(
            f"{self.type.value} has been manually turned to '{turn_to.value}'"
            f"{additional_message}")
        if self._status != self._last_status or self._mode != self._last_mode:
            self.logger.info(
                f"{self.type.value.capitalize()} has been turned "
                f"{'on' if self.status else 'off'} with '{self.mode.value}' mode")
            self.send_actuators_state()
        self._last_mode = self.mode
        self._last_status = self.status

    def send_actuators_state(self) -> None:
        if (
                self.ecosystem.engine.use_message_broker
                and self.ecosystem.event_handler.registered
        ):
            self.ecosystem.logger.debug(
                "Sending actuators data to Ouranos")
            try:
                self.ecosystem.event_handler.send_actuator_data(
                    ecosystem_uids=self.ecosystem.config.uid)
            except Exception as e:
                msg = e.args[1] if len(e.args) > 1 else e.args[0]
                if "is not a connected namespace" in msg:
                    pass
                self.logger.error(
                    f"Encountered an error while sending actuator data. "
                    f"ERROR msg: `{e.__class__.__name__} :{e}`"
                )

    def compute_expected_status(self, **kwargs) -> bool:
        countdown = self.countdown
        if countdown is not None and countdown <= 0.1:
            self.set_mode(gv.ActuatorMode.automatic)
            self.reset_countdown()
        if self.mode == gv.ActuatorMode.automatic:
            return self._expected_status_function(**kwargs)
        else:
            if self.status:
                return True
            return False


class ActuatorHandlers:
    def __init__(self, ecosystem: "Ecosystem") -> None:
        self.ecosystem: Ecosystem = weakref.proxy(ecosystem)
        self._handlers: dict[gv.HardwareType, ActuatorHandler] = {}
        self._populate_actuators()

    def _populate_actuators(self) -> None:
        for hardware_type in gv.HardwareType:
            if hardware_type == gv.HardwareType.sensor:
                continue
            elif hardware_type == gv.HardwareType.light:
                self._handlers[hardware_type] = ActuatorHandler(
                    self.ecosystem,
                    hardware_type,
                    Light.expected_status
                )
            elif hardware_type in (
                    gv.HardwareType.heater, gv.HardwareType.cooler,
                    gv.HardwareType.humidifier, gv.HardwareType.dehumidifier,
            ):
                self._handlers[hardware_type] = ActuatorHandler(
                    self.ecosystem,
                    hardware_type,
                    Climate.expected_status
                )

    def get_handler(self, actuator_type: gv.HardwareType | gv.HardwareTypeNames):
        actuator_type = gv.safe_enum_from_name(gv.HardwareType, actuator_type)
        if actuator_type == gv.HardwareType.sensor:
            raise ValueError(f"Actuator type {actuator_type} is not valid.")
        return self._handlers[actuator_type]

    def as_dict(self) -> gv.ActuatorsDataDict:
        return {
            actuator_type.name: handler.as_dict()
            for actuator_type, handler in self._handlers.items()
        }
