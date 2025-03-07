from __future__ import annotations

import asyncio
from asyncio import Future, Lock, Task, TimerHandle, CancelledError
from contextlib import asynccontextmanager
import enum
import dataclasses
from datetime import datetime, timezone
from functools import partial
import logging
import time
import typing
from typing import Awaitable, Callable, Type

import gaia_validators as gv

from gaia.hardware.abc import Dimmer, Hardware, Switch


if typing.TYPE_CHECKING:
    from gaia import Ecosystem
    from gaia.database.models import ActuatorBuffer, ActuatorRecord


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


def generate_hardware_to_parameter_dict() -> dict[gv.HardwareType, gv.ClimateParameter]:
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
            hysteresis: float = 0.0,
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
        self.hysteresis: float = hysteresis
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

    def __repr__(self) -> str:  # pragma: no cover
        uid = self.actuator_hub.ecosystem.uid
        return f"{self.__class__.__name__}({uid}, parameter={self.climate_parameter})"

    @staticmethod
    def clamp(
            value: float,
            lower_limit: float | None,
            higher_limit: float | None,
    ) -> float:
        if lower_limit is not None:
            value = max(value, lower_limit)
        if higher_limit is not None:
            value = min(value, higher_limit)
        return value

    @property
    def direction(self) -> Direction:
        if self._direction is not None:
            return self._direction
        direction: Direction = Direction.none
        actuator_couple: ActuatorCouple = actuator_couples[self.climate_parameter]
        for direction_name, actuator_type in actuator_couple.items():
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

    def update_pid(self, current_value: float | None) -> float:
        if current_value is None:
            # Set output to 0 and refresh the old sampling time
            sampling_time = self._last_sampling_time
            output = 0.0

        else:
            # Compute output
            sampling_time = time.monotonic()
            output = None
            if self.hysteresis:
                output = self._hysteresis_internal(current_value)
            if output is None:
                output = self._pid_internal(current_value, sampling_time)

        # Update the internal state
        self._last_sampling_time = sampling_time
        self._last_input = current_value
        self._last_output = output

        # Debug
        if self.actuator_hub.logger.level <= logging.DEBUG:
            if output > 0.0 and not self.direction | Direction.increase:
                self.actuator_hub.logger.debug(
                    f"PID output for {self.climate_parameter.name} is > 0 but no"
                    f"actuator able to increase {self.climate_parameter.name} "
                    f"has been detected. {self.climate_parameter.name.capitalize()} "
                    f"may remain under the targeted value."
                )
            if output < 0.0 and not self.direction | Direction.decrease:
                self.actuator_hub.logger.debug(
                    f"PID output for {self.climate_parameter.name} is < 0 but no"
                    f"actuator able to decrease {self.climate_parameter.name} "
                    f"has been detected. {self.climate_parameter.name.capitalize()} "
                    f"may remain above the targeted value."
                )
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
        output = (error * self.Kp) + (integral * self.Ki) + (derivative * self.Kd)
        return self.clamp(output, self.minimum_output, self.maximum_output)

    def _reset_errors(self) -> None:
        self._integrator = []
        self._last_error = 0.0

    def reset(self) -> None:
        self._last_input = None
        self._last_sampling_time = None
        self._last_output = 0.0
        self._reset_errors()


class Timer:
    def __init__(self, callback: Awaitable | Callable, countdown: float) -> None:
        self._start_time: float = time.monotonic()
        self._countdown: float = 0.0
        self._task: Task = asyncio.create_task(self._job(callback))
        self._future: Future = Future()
        self._handle: TimerHandle | None = None
        self.modify_countdown(countdown)

    @property
    def done(self) -> bool:
        return self._task.done()

    @property
    def cancelled(self) -> bool:
        return self._task.cancelled()

    async def _job(self, callback: Awaitable | Callable) -> None:
        await self._future
        if asyncio.iscoroutinefunction(callback):
            await callback()
        else:
            callback()

    def cancel(self) -> None:
        self._task.cancel()
        self._handle.cancel()
        self._future.cancel()

    def time_left(self) -> float | None:
        if self.done or self.cancelled:
            return None
        return self._start_time + self._countdown - time.monotonic()

    def modify_countdown(self, countdown_delta: float) -> None:
        if self.done:
            raise RuntimeError("The task has already been completed")
        if self.cancelled:
            raise CancelledError("The task has been canceled")
        self._countdown += countdown_delta
        loop = asyncio.get_running_loop()
        if self._handle:
            self._handle.cancel()
        self._handle = loop.call_later(self.time_left(), self._future.set_result, None)


class ActuatorHandler:
    __slots__ = (
        "_active",
        "_actuators",
        "_any_status_change",
        "_last_expected_level",
        "_level",
        "_mode",
        "_sending_data_task",
        "_status",
        "_timer",
        "_update_lock",
        "_updating",
        "actuator_hub",
        "direction",
        "ecosystem",
        "logger",
        "type",
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
        self._timer: Timer | None = None
        self._actuators: list[Switch | Dimmer] | None = None
        self._last_expected_level: float | None = None
        self._update_lock: Lock = Lock()
        self._updating: bool = False
        self._any_status_change: bool = False
        self._sending_data_task: Task | None = None

    def __repr__(self) -> str:  # pragma: no cover
        uid = self.actuator_hub.ecosystem.uid
        return f"ActuatorHandler({uid}, actuator_type={self.type.name})"

    def get_linked_actuators(self) -> list[Switch | Dimmer]:
        if self._actuators is None:
            self._actuators = [
                hardware
                for hardware in Hardware.get_mounted().values()
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

    def as_record(self, timestamp: datetime) -> gv.ActuatorStateRecord:
        return gv.ActuatorStateRecord(
            type=self.type,
            active=self.active,
            mode=self.mode,
            status=self.status,
            level=self._level,
            timestamp=timestamp,
        )

    @property
    def active(self) -> bool:
        return self._active > 0

    def activate(self) -> None:
        self._check_actuator_available()
        if self._active == 0:
            self._any_status_change = True
        self._active += 1

    def deactivate(self) -> None:
        self._active -= 1
        if self._active == 0:
            self._any_status_change = True
        if self._active < 0:
            raise RuntimeError(
                "Cannot deactivate an actuator more times than it has been "
                "activated."
            )

    def _check_actuator_available(self) -> None:
        if not self.ecosystem.config.get_IO_group_uids(self.type):
            raise RuntimeError(
                f"No actuator '{self.type.name}' available in the config file."
            )

    def _check_active(self) -> None:
        if self._active == 0:
            raise RuntimeError("This actuator is not active.")

    @asynccontextmanager
    async def update_status_transaction(self, activation: bool = False):
        async with self._update_lock:
            try:
                self._updating = True
                self._any_status_change = False
                if not activation:
                    self._check_active()
                yield
            except Exception:
                raise
            finally:
                if self._any_status_change:
                    updated_data = self.as_record(datetime.now(timezone.utc))
                    await self.log_actuator_state(updated_data)
                    await self.schedule_send_actuator_state(updated_data)
                if self._timer is not None:
                    if self._timer.time_left is None:
                        self.reset_timer()
                self._updating = False

    def _check_update_status_transaction(self) -> None:
        if not self._updating:
            raise RuntimeError(
                "This method should be used in a 'update_status' `async with` block."
            )

    @property
    def mode(self) -> gv.ActuatorMode:
        return self._mode

    async def set_mode(self, value: gv.ActuatorMode) -> None:
        self._check_update_status_transaction()
        validated_value = gv.safe_enum_from_name(gv.ActuatorMode, value)
        if self._mode == validated_value:
            # No need to update
            return
        self._set_mode(validated_value)
        self._any_status_change = True
        self.logger.info(
            f"{self.type.name.capitalize()} has been set to "
            f"'{self.mode.name}' mode.")

    def _set_mode(self, value: gv.ActuatorMode) -> None:
        self._mode = value
        pid: HystericalPID = self.get_associated_pid()
        pid.reset()

    @property
    def status(self) -> bool:
        return self._status

    async def set_status(self, value: bool) -> None:
        self._check_update_status_transaction()
        if self._status == value:
            # No need to update
            return
        await self._set_status(value)
        self._any_status_change = True
        self.logger.info(
            f"{self.type.name.capitalize()} has been turned "
            f"{'on' if self.status else 'off'}.")

    async def _set_status(self, value: bool) -> None:
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
                    await actuator.turn_on()
                else:
                    await actuator.turn_off()

    async def turn_on(self) -> None:
        await self.set_status(True)

    async def turn_off(self) -> None:
        await self.set_status(False)

    @property
    def level(self) -> float | None:
        return self._level

    async def set_level(self, pwm_level: float) -> None:
        self._check_update_status_transaction()
        if self._level == pwm_level:
            return
        await self._set_level(pwm_level)
        #self._any_status_change = True
        self.logger.debug(
            f"{self.type.name.capitalize()}'s level has been set to {pwm_level}%.")

    async def _set_level(self, pwm_level: float) -> None:
        self._level = pwm_level
        for actuator in self.get_linked_actuators():
            if isinstance(actuator, Dimmer):
                await actuator.set_pwm_level(pwm_level)

    @property
    def countdown(self) -> float | None:
        if self._timer is None:
            return None
        return self._timer.time_left()

    def reset_timer(self) -> None:
        self._check_update_status_transaction()
        if self._timer is not None:
            self._timer.cancel()
        self._timer = None
        self._any_status_change = True

    def increase_countdown(self, delta_time: float) -> None:
        self._check_update_status_transaction()
        if self._timer is None:
            raise AttributeError("No timer set, you cannot increase the countdown.")
        self.logger.info(f"Increasing timer by {delta_time} seconds.")
        self._timer.modify_countdown(delta_time)
        self._any_status_change = True

    def decrease_countdown(self, delta_time: float) -> None:
        self._check_update_status_transaction()
        if self._timer is None:
            raise AttributeError("No timer set, you cannot decrease the countdown.")
        self.logger.info(f"Decreasing timer by {delta_time} seconds.")
        self._timer.modify_countdown(-delta_time)
        self._any_status_change = True

    async def _turn_to(self, turn_to: gv.ActuatorModePayload) -> None:
        if turn_to == gv.ActuatorModePayload.automatic:
            await self.set_mode(gv.ActuatorMode.automatic)
            outdated_expected_status = self.compute_expected_status(
                self._last_expected_level)
            await self.set_status(outdated_expected_status)
        else:
            await self.set_mode(gv.ActuatorMode.manual)
            if turn_to == gv.ActuatorModePayload.on:
                await self.set_status(True)
            else:  # turn_to == ActuatorModePayload.off
                await self.set_status(False)
        if self._any_status_change:
            self.logger.info(
                f"{self.type.name.capitalize()} has been turned to "
                f"'{turn_to.name}'.")

    async def _transactional_turn_to(self, turn_to: gv.ActuatorModePayload) -> None:
        async with self.update_status_transaction():
            await self._turn_to(turn_to)

    async def turn_to(
            self,
            turn_to: gv.ActuatorModePayload,
            countdown: float | None = None,
    ) -> None:
        self._check_update_status_transaction()
        turn_to: gv.ActuatorModePayload = gv.safe_enum_from_name(
            gv.ActuatorModePayload, turn_to)
        if self._timer is not None:
            self.logger.warning(
                f"{self.type.name.capitalize()}'s timer already set, resetting "
                f"it for {turn_to.name}."
            )
            self.reset_timer()
        if countdown:
            self.logger.info(
                f"{self.type.name.capitalize()} will be turned to "
                f"'{turn_to.name}' in {countdown} seconds.")
            callback = partial(self._transactional_turn_to, turn_to)
            self._timer = Timer(callback, countdown)
        else:
            await self._turn_to(turn_to)

    async def _log_actuator_state(
            self,
            data: gv.ActuatorStateRecord,
            db_model: Type[ActuatorRecord] | Type[ActuatorBuffer],
    ) -> None:
        async with self.ecosystem.engine.db.scoped_session() as session:
            session.add(
                db_model(
                    ecosystem_uid=self.ecosystem.uid,
                    type=data.type,
                    timestamp=data.timestamp,
                    active=data.active,
                    mode=data.mode,
                    status=data.status,
                    level=None,
                )
            )
            await session.commit()

    async def log_actuator_state(
            self,
            data: gv.ActuatorStateRecord | None = None,
    ) -> None:
        if not self.ecosystem.engine.use_db:
            return
        if data is None:
            data = self.as_record(datetime.now(timezone.utc))
        from gaia.database.models import ActuatorRecord

        await self._log_actuator_state(data, ActuatorRecord)

    async def send_actuator_state(
            self,
            data: gv.ActuatorStateRecord | None = None,
    ) -> None:
        # Check if we use the message broker
        if not self.ecosystem.engine.use_message_broker:
            return
        # Get the actuator data if needed
        if data is None:
            data = self.as_record(datetime.now(timezone.utc))
        # Check whether we can send the actuator data
        sent: bool = False
        try:
            # Can be cancelled if it takes too long
            if self.ecosystem.event_handler.is_connected():
                payload = gv.ActuatorsDataPayload(
                    uid=self.ecosystem.uid,
                    data=[data],
                ).model_dump()
                sent = await self.ecosystem.engine.event_handler.emit(
                    "actuators_data",
                    data=[payload],
                )
        # If the data wasn't sent, and the db is enabled, save the data in the db buffer
        finally:
            if not sent and self.ecosystem.engine.use_db:
                from gaia.database.models import ActuatorBuffer

                await self._log_actuator_state(data, ActuatorBuffer)

    async def schedule_send_actuator_state(
            self,
            data: gv.ActuatorStateRecord | None = None,
    ) -> None:
        if not (
                self._sending_data_task is None
                or self._sending_data_task.done()
        ):
            self.logger.warning(
                "There is already an actuator state sending task running. It "
                "will be cancelled to start a new one."
            )
            self._sending_data_task.cancel()
        task_name = f"{self.ecosystem.uid}-{self.type.name}_actuator-send_data"
        self._sending_data_task = asyncio.create_task(
            self.send_actuator_state(data), name=task_name)

    def compute_expected_status(self, expected_level: float) -> bool:
        self._last_expected_level = expected_level
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
            # Mode is manual
            return self.status


class ActuatorHub:
    def __init__(self, ecosystem: "Ecosystem") -> None:
        self.ecosystem: Ecosystem = ecosystem
        self.logger = logging.getLogger(
            f"gaia.engine.{ecosystem.name.replace(' ', '_')}.actuators")
        self._pids: dict[gv.ClimateParameter, HystericalPID] = {}
        self._populate_pids()
        self._actuator_handlers: dict[gv.HardwareType.actuator, ActuatorHandler] = {}
        self._populate_actuators()

    def _populate_pids(self) -> None:
        for climate_parameter in gv.ClimateParameter:
            climate_parameter: gv.ClimateParameter
            pid_parameters = pid_values[climate_parameter]
            self._pids[climate_parameter] = HystericalPID(
                self,
                climate_parameter,
                Kp=pid_parameters.Kp,
                Ki=pid_parameters.Ki,
                Kd=pid_parameters.Kd,
                minimum_output=-100.0,
                maximum_output=100.0,
            )

    def _populate_actuators(self) -> None:
        for actuator_couple in actuator_couples.values():
            for direction_name, actuator_type in actuator_couple.items():
                if actuator_type is None:
                    continue
                self._actuator_handlers[actuator_type] = ActuatorHandler(
                    self, actuator_type, Direction[direction_name])

    def get_pid(
            self,
            climate_parameter: gv.ClimateParameter,
    ) -> HystericalPID:
        climate_parameter = gv.safe_enum_from_name(gv.ClimateParameter, climate_parameter)
        return self._pids[climate_parameter]

    def get_handler(
            self,
            actuator_type: gv.HardwareType,
    ) -> ActuatorHandler:
        actuator_type = gv.safe_enum_from_name(gv.HardwareType, actuator_type)
        assert actuator_type in gv.HardwareType.actuator
        return self._actuator_handlers[actuator_type]

    def as_dict(self) -> dict[gv.HardwareType.actuator, gv.ActuatorStateDict]:
        return {
            actuator_type.name: handler.as_dict()
            for actuator_type, handler in self._actuator_handlers.items()
        }

    def as_records(self) -> list[gv.ActuatorStateRecord]:
        now = datetime.now(timezone.utc)
        return [
            handler.as_record(now)
            for handler in self._actuator_handlers.values()
        ]
