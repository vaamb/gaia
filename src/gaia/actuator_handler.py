from __future__ import annotations

import enum
import dataclasses
import logging
import time
import typing
import weakref

import gaia_validators as gv

from gaia.hardware.abc import Dimmer, Hardware, Switch


if typing.TYPE_CHECKING:
    from gaia import Ecosystem


@dataclasses.dataclass(frozen=True)
class ActuatorCouple:
    increase: gv.HardwareType | None
    decrease: gv.HardwareType | None

    def __iter__(self) -> typing.Iterable[gv.HardwareType | None]:
        return iter((self.increase, self.decrease))

    def __getitem__(self, key: str) -> gv.HardwareType | None:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(f"{key}")

    def items(self) -> typing.ItemsView[str, gv.HardwareType | None]:
        return self.__dict__.items()

    @staticmethod
    def directions() -> tuple[str, str]:
        return "increase", "decrease"


actuator_couples: dict[gv.ClimateParameter: ActuatorCouple] = {
    gv.ClimateParameter.temperature: ActuatorCouple(
        gv.HardwareType.heater, gv.HardwareType.cooler),
    gv.ClimateParameter.humidity: ActuatorCouple(
        gv.HardwareType.humidifier, gv.HardwareType.dehumidifier),
    gv.ClimateParameter.light: ActuatorCouple(
        gv.HardwareType.light, None),
    gv.ClimateParameter.wind: ActuatorCouple(
        gv.HardwareType.fan, None)
}


def generate_hardware_to_parameter_dict() -> dict[gv.HardwareType.actuator, gv.ClimateParameter]:
    rv = {}
    for climate_parameter, actuator_couple in actuator_couples.items():
        for direction in actuator_couple:
            if direction is None:
                continue
            rv[direction] = climate_parameter
    return rv


hardware_to_parameter = generate_hardware_to_parameter_dict()


class PIDParameters(typing.NamedTuple):
    Kp: float
    Ki: float
    Kd: float


pid_values: dict[gv.ClimateParameter: PIDParameters] = {
    gv.ClimateParameter.temperature: PIDParameters(2.0, 0.5, 1.0),
    gv.ClimateParameter.humidity: PIDParameters(2.0, 0.5, 1.0),
    gv.ClimateParameter.light: PIDParameters(0.001, 0.0, 0.0),
    gv.ClimateParameter.wind: PIDParameters(1.0, 0.0, 0.0),
}


class Direction(enum.IntFlag):
    none = 0
    decrease = 1
    increase = 2
    both = decrease | increase


class HystericalPID:
    """A PID able to take hysteresis into account."""
    def __init__(
            self,
            actuator_hub: ActuatorHub,
            climate_parameter: gv.ClimateParameter,
            target: float = 0.0,
            hysteresis: float | None = None,
            Kp: float = 1.0,
            Ki: float = 0.0,
            Kd: float = 0.0,
            minimum_output: float | None = None,
            maximum_output: float | None = None,
            integration_period: int = 10,
            used_regularly: bool = True,
    ) -> None:
        self.actuator_hub: ActuatorHub = actuator_hub
        self.climate_parameter: gv.ClimateParameter = climate_parameter
        self.target: float = target
        self.hysteresis: float | None = hysteresis
        self.Kp: float = Kp
        self.Ki: float = Ki
        self.Kd: float = Kd
        self.minimum_output: float | None = minimum_output
        self.maximum_output: float | None = maximum_output
        self._direction: Direction | None = None
        self._last_sampling_time: float | None = None
        self._last_error: float = 0.0
        self._integrator: list[float] = []
        self._integration_period: int = integration_period
        self._used_regularly: bool = used_regularly
        self._last_input: float | None = None
        self._last_output: float = 0.0

    def __repr__(self) -> str:
        uid = self.actuator_hub.ecosystem.uid
        return f"{self.__class__.__name__}({uid}, parameter={self.climate_parameter})"

    @staticmethod
    def clamp(
            value: float,
            lower_limit: float | None,
            higher_limit: float | None
    ) -> float:
        if (lower_limit is not None) and (value < lower_limit):
            return lower_limit
        elif (higher_limit is not None) and (value > higher_limit):
            return higher_limit
        return value

    @property
    def direction(self) -> Direction:
        if self._direction is not None:
            return self._direction
        direction: Direction = Direction.none
        actuator_couple: ActuatorCouple = actuator_couples[self.climate_parameter]
        for direction_name in actuator_couple.directions():
            actuator_type = actuator_couple[direction_name]
            if actuator_type is None:
                continue
            actuator_handler: ActuatorHandler = self.actuator_hub.get_handler(
                actuator_type)
            if actuator_handler.get_linked_actuators():
                direction = direction | Direction[direction_name]
        self._direction = direction
        return direction

    def reset_direction(self) -> None:
        self._direction = None

    @property
    def last_output(self) -> float:
        return self._last_output

    def update_pid(self, current_value: float) -> float:
        sampling_time = time.monotonic()
        output = None

        if self.hysteresis is not None:
            output = self._hysteresis_internal(current_value)
        if output is None:
            output = self._pid_internal(current_value, sampling_time)
        self._last_sampling_time = sampling_time
        self._last_input = current_value
        self._last_output = output
        return output

    def _hysteresis_internal(self, current_value: float) -> float | None:
        target_min = self.target - self.hysteresis
        target_max = self.target + self.hysteresis

        if self.last_output == 0:
            if target_min <= current_value <= target_max:
                self._reset_errors()
                return 0.0
            else:  # Out ouf targeted range, need PID
                return None

        elif self.last_output > 0:
            if self.target <= current_value <= target_max:
                self._reset_errors()
                return 0.0
            else:  # Out ouf targeted range, need PID
                return None

        elif self.last_output < 0:
            if target_min <= current_value <= self.target:
                self._reset_errors()
                return 0.0
            else:  # Out ouf targeted range, need PID
                return None

    def _pid_internal(self, current_value: float, sampling_time: float) -> float:
        if self._last_sampling_time is None or self._used_regularly:
            delta_time = 1
        else:
            delta_time = sampling_time - self._last_sampling_time

        error = self.target - current_value
        delta_error = error - self._last_error
        self._last_error = error

        # Integral-related computation
        self._integrator.append(error * delta_time)
        if len(self._integrator) > self._integration_period:
            self._integrator = self._integrator[-self._integration_period:]
        integral = sum(self._integrator)
        # Derivative-related computation
        derivative = delta_error / delta_time
        # Compute output
        output = (error * self.Kp) + (integral * self. Ki) + (derivative * self.Kd)
        return self.clamp(output, self.minimum_output, self.maximum_output)

    def _reset_errors(self) -> None:
        self._integrator = []
        self._last_error = 0.0

    def reset(self) -> None:
        self._last_input = None
        self._last_sampling_time = None
        self._last_output = 0.0
        self._reset_errors()


def always_off(**kwargs) -> bool:
    return False


class ActuatorHandler:
    __slots__ = (
        "_active", "_actuators", "_expected_status_function", "_level", "_last_mode",
        "_last_status", "_mode", "_status", "_time_limit", "_timer_on",
        "ecosystem", "actuator_hub", "direction", "logger", "type"
    )

    def __init__(
            self,
            actuator_hub: ActuatorHub,
            actuator_type: gv.HardwareType,
            actuator_direction: Direction,
    ) -> None:
        assert actuator_type in gv.HardwareType.actuator
        assert actuator_direction in (Direction.decrease, Direction.increase)
        self.actuator_hub: ActuatorHub = actuator_hub
        self.ecosystem = self.actuator_hub.ecosystem
        self.type: gv.HardwareType = actuator_type
        self.direction: Direction = actuator_direction
        eco_name = self.ecosystem.name.replace(" ", "_")
        self.logger = logging.getLogger(
            f"gaia.engine.{eco_name}.actuators.{self.type.name}")
        self._active: int = 0
        self._status: bool = False
        self._level: float | None = None
        self._mode: gv.ActuatorMode = gv.ActuatorMode.automatic
        self._timer_on: bool = False
        self._time_limit: float = 0.0
        self._last_status: bool = self.status
        self._last_mode: gv.ActuatorMode = self.mode
        self._actuators: list[Switch | Dimmer] | None = None

    def __repr__(self) -> str:
        uid = self.actuator_hub.ecosystem.uid
        return f"ActuatorHandler({uid}, actuator_type={self.type.name})"

    def get_linked_actuators(self) -> list[Switch | Dimmer]:
        if self._actuators is None:
            self._actuators = [
                hardware for hardware in Hardware.get_mounted().values()
                if hardware.ecosystem_uid == self.ecosystem.uid
                and hardware.type == self.type
            ]
        return self._actuators

    # TODO: use when update hardware
    def reset_cached_actuators(self) -> None:
        self._actuators = None
        pid: HystericalPID = self.get_associated_pid()
        pid.reset_direction()

    def get_associated_pid(self) -> HystericalPID:
        climate_parameter = hardware_to_parameter[self.type]
        return self.actuator_hub.get_pid(climate_parameter)

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
            # TODO: reset associated PID ?
            self.logger.info(
                f"{self.type.name.capitalize()} has been set to "
                f"'{self.mode.name}' mode")
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
                f"{self.type.name.capitalize()} has been turned "
                f"{'on' if self.status else 'off'}")
            self.send_actuators_state()
            self._last_status = self._status

    def turn_on(self) -> None:
        self.set_status(True)

    def turn_off(self) -> None:
        self.set_status(False)

    def _set_status_no_update(self, value: bool) -> None:
        self._status = value
        actuators_linked = self.get_linked_actuators()
        if not actuators_linked:
            raise RuntimeError(
                f"{self.type.name.capitalize()} handler cannot be turned "
                f"{'on' if self.status else 'off'} as it has no actuator linked "
                f"to it."
            )
        for actuator in actuators_linked:
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
        for actuator in self.get_linked_actuators():
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

    def check_countdown(self) -> None:
        countdown = self.countdown
        if countdown is not None and countdown <= 0.1:
            self.set_mode(gv.ActuatorMode.automatic)
            self.reset_countdown()

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
                # TODO: use a callback ?
        self.logger.info(
            f"{self.type.name} has been manually turned to '{turn_to.name}'"
            f"{additional_message}.")
        if self._status != self._last_status or self._mode != self._last_mode:
            self.logger.info(
                f"{self.type.name.capitalize()} has been turned "
                f"{'on' if self.status else 'off'} with '{self.mode.name}' mode.")
            self.send_actuators_state()
        self._last_mode = self.mode
        self._last_status = self.status

    def send_actuators_state(self) -> None:
        if (
                self.ecosystem.engine.use_message_broker
                and self.ecosystem.event_handler.registered
        ):
            self.ecosystem.event_handler.send_actuator_data(
                ecosystem_uids=self.ecosystem.config.uid)

    def compute_expected_status(self, expected_level: float | None) -> bool:
        self.check_countdown()
        if self.mode == gv.ActuatorMode.automatic:
            if expected_level is None:
                self.logger.error(
                    "Cannot compute an expected status for automatic mode "
                    "without an expected PID level. Falling back to off status.")
                return False
            else:
                if self.direction == Direction.increase:
                    return expected_level > 0  # Should be on when trying to increase measure
                else:
                    return expected_level < 0  # Should be on when trying to decrease measure
        else:
            if self.status:
                return True
            return False


class ActuatorHub:
    def __init__(self, ecosystem: "Ecosystem") -> None:
        self.ecosystem: Ecosystem = weakref.proxy(ecosystem)
        self._pids: dict[gv.ClimateParameter, HystericalPID] = {}
        self._populate_pids()
        self._actuator_handlers: dict[gv.HardwareType.actuator, ActuatorHandler] = {}
        self._populate_actuators()

    def _populate_pids(self) -> None:
        for climate_parameter in gv.ClimateParameter:
            climate_parameter: gv.ClimateParameter
            pid_parameters = pid_values[climate_parameter]
            self._pids[climate_parameter] = HystericalPID(
                self, climate_parameter,
                Kp=pid_parameters.Kp, Ki=pid_parameters.Ki, Kd=pid_parameters.Kd,
                minimum_output=-100.0, maximum_output=100.0,
            )

    def _populate_actuators(self) -> None:
        for actuator_couple in actuator_couples.values():
            for direction, actuator_type in actuator_couple.items():
                if actuator_type is None:
                    continue
                self._actuator_handlers[actuator_type] = ActuatorHandler(
                    self, actuator_type, Direction[direction])

    def get_pid(
            self,
            climate_parameter: gv.ClimateParameter | gv.ClimateParameterNames
    ) -> HystericalPID:
        climate_parameter = gv.safe_enum_from_name(gv.ClimateParameter, climate_parameter)
        return self._pids[climate_parameter]

    def get_handler(
            self,
            actuator_type: gv.HardwareType | gv.HardwareTypeNames
    ) -> ActuatorHandler:
        actuator_type = gv.safe_enum_from_name(gv.HardwareType, actuator_type)
        assert actuator_type in gv.HardwareType.actuator
        return self._actuator_handlers[actuator_type]

    def as_dict(self) -> gv.ActuatorsDataDict:
        return {
            actuator_type.name: handler.as_dict()
            for actuator_type, handler in self._actuator_handlers.items()
        }
