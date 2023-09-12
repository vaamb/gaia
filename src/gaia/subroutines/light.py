from __future__ import annotations

from asyncio import create_task, sleep, Task
from datetime import datetime, time
from statistics import mean

from simple_pid import PID

from gaia_validators import (
    ActuatorModePayload, HardwareConfig, HardwareType, LightingHours,
    LightMethod)

from gaia.config import get_config
from gaia.exceptions import UndefinedParameter
from gaia.hardware import actuator_models
from gaia.hardware.abc import Dimmer, Hardware, LightSensor, Switch
from gaia.actuator_handler import ActuatorHandler
from gaia.subroutines.template import SubroutineTemplate


Kp = 0.05
Ki = 0.005
Kd = 0.01


# TODO: improve
def _is_time_between(
        begin_time: time,
        end_time: time,
        check_time: time | None = None
) -> bool:
    check_time = check_time or datetime.now().astimezone().time()
    try:
        if begin_time < end_time:
            return begin_time <= check_time < end_time
        else:  # crosses midnight
            return check_time >= begin_time or check_time < end_time
    except TypeError:
        # one of times is a none
        return False


class Light(SubroutineTemplate):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hardware_choices = actuator_models
        self.hardware: dict[str, Switch]
        self._light_status_task: Task | None = None
        self._light_intensity_task: Task | None = None
        self.actuator: ActuatorHandler = ActuatorHandler(
            self, HardwareType.light, self.expected_status)
        self._dimmers: set[str] = set()
        self._pid = PID(Kp, Ki, Kd)
        self._finish__init__()

    @staticmethod
    async def expected_status(
            *,
            method: LightMethod,
            lighting_hours: LightingHours
    ) -> bool:
        now: time = datetime.now().astimezone().time()
        if method == LightMethod.elongate:
            # If time between lightning hours
            if (
                lighting_hours.morning_start <= now <= lighting_hours.morning_end
                or
                lighting_hours.evening_start <= now <= lighting_hours.evening_end
            ):
                return True
            else:
                return False
        else:
            return _is_time_between(
                lighting_hours.morning_start,
                lighting_hours.evening_end,
                check_time=now
            )

    async def _light_status_loop(self) -> None:
        cfg = get_config()
        self.logger.info(
            f"Starting light loop at a frequency of {1/cfg.LIGHT_LOOP_PERIOD} Hz")
        while True:
            await self._light_status_routine()
            await sleep(cfg.LIGHT_LOOP_PERIOD)

    async def _light_status_routine(self) -> None:
        # If lighting == True, lights should be on
        async with self.ecosystem.lighting_hours_lock:
            lighting_hours = self.lighting_hours
        lighting = await self.actuator.compute_expected_status(
            method=self.ecosystem.light_method,
            lighting_hours=lighting_hours,
        )
        light: Switch
        if lighting:
            # Reset pid so there is no internal value overshoot
            if not self.actuator.last_status:
                self._pid.reset()
            await self.actuator.set_status(True)
            for light in self.hardware.values():
                await light.turn_on()
                await sleep(0)
        # If lighting == False, lights should be off
        else:
            await self.actuator.set_status(False)
            for light in self.hardware.values():
                await light.turn_off()
                await sleep(0)

    # TODO: add a second loop for light level, only used if light is on and dimmable
    async def _light_intensity_loop(self) -> None:
        if self.ecosystem.get_subroutine_status("sensors"):
            while True:
                light_sensors: list[LightSensor] = [
                    sensor for sensor in
                    Hardware.get_actives_by_type(HardwareType.sensor)
                    if isinstance(sensor, LightSensor)
                ]
                light_level: list[float] = []
                for light_sensor in light_sensors:
                    light = await light_sensor.get_lux()
                    if light is not None:
                        light_level.append(light)
                mean_light = mean(light_level)
                await self._light_intensity_routine(mean_light)
                await sleep(1)

    async def _light_intensity_routine(self, light_level: float) -> None:
        pass

    """Functions to switch the light on/off either manually or automatically"""
    def _update_manageable(self) -> None:
        try:
            time_parameters = bool(self.config.time_parameters)
        except UndefinedParameter:
            time_parameters = False
        if all((
                self.config.get_IO_group_uids("light"),
                self.ecosystem.light_method,
                time_parameters
        )):
            self.manageable = True
        else:
            self.logger.warning(
                "At least one of light hardware, lighting method, or time "
                "parameters is missing. Disabling Light subroutine"
            )
            self.manageable = False

    def _start(self) -> None:
        self.light_status_task = create_task(
            self._light_status_loop(),
            name=f"{self.ecosystem.uid}-light-status")
        # self.light_intensity_task = create_task(
        #     self._light_intensity_loop(),
        #     name=f"{self.ecosystem.name}-light-intensity")
        self.actuator.active = True

    def _stop(self) -> None:
        self.logger.info("Stopping light loop")
        self.light_status_task.cancel()
        self.light_status_task = None
        # self.light_intensity_task.cancel()
        # self.light_intensity_task = None
        self.actuator.active = False
        self.hardware = {}

    """API calls"""
    @property
    def light_status_task(self) -> Task:
        if self._light_status_task is None:
            raise AttributeError("Light status task has not been set up")
        else:
            return self._light_status_task

    @light_status_task.setter
    def light_status_task(self, task: Task | None) -> None:
        self._light_status_task = task

    @property
    def light_intensity_task(self) -> Task:
        if self._light_intensity_task is None:
            raise AttributeError("Light intensity task has not been set up")
        else:
            return self._light_intensity_task

    @light_intensity_task.setter
    def light_intensity_task(self, task: Task | None) -> None:
        self._light_intensity_task = task

    async def add_hardware(self, hardware_config: HardwareConfig) -> Hardware:
        hardware = await super().add_hardware(hardware_config)
        if isinstance(hardware, Dimmer):
            self._dimmers.add(hardware.uid)
        return hardware

    async def remove_hardware(self, hardware_uid: str) -> None:
        await super().remove_hardware(hardware_uid)
        if hardware_uid in self._dimmers:
            self._dimmers.remove(hardware_uid)

    def get_hardware_needed_uid(self) -> set[str]:
        return set(self.config.get_IO_group_uids("light"))

    @property
    def lighting_hours(self) -> LightingHours:
        return self.ecosystem.lighting_hours

    async def turn_light(
            self,
            turn_to: ActuatorModePayload = ActuatorModePayload.automatic,
            countdown: float = 0.0
    ) -> None:
        if self._started:
            await self.actuator.turn_to(turn_to, countdown)
        else:
            raise RuntimeError(
                f"{self.name} is not started in ecosystem {self.ecosystem}")

    @property
    def PID_tunings(self) -> tuple:
        """Returns the tunings used by the controller as a tuple: (Kp, Ki, Kd)"""
        return self._pid.tunings

    @PID_tunings.setter
    def PID_tunings(self, tunings: tuple) -> None:
        """:param tunings: tuple (Kp, Ki, Kd)"""
        self._pid.tunings = tunings
