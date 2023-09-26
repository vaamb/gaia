from __future__ import annotations

from datetime import datetime
import io
import typing as t

from gaia_validators import (
    ActuatorMode, ActuatorModePayload, Empty, HealthRecord)

from gaia.config import get_config
from gaia.hardware import camera_models
from gaia.hardware.abc import Camera
from gaia.shared_resources import get_scheduler
from gaia.subroutines.template import SubroutineTemplate


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.subroutines.light import Light


class Health(SubroutineTemplate):
    # TODO: fix
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hardware_choices = camera_models
        self.hardware: dict[str, Camera]
        self._plants_health: HealthRecord | Empty = Empty()
        self._imageIO = io.BytesIO()
        self._finish__init__()

    def _start_scheduler(self) -> None:
        h, m = get_config().HEALTH_LOGGING_TIME.split("h")
        scheduler = get_scheduler()
        scheduler.add_job(
            self.health_routine,
            trigger="cron", hour=h, minute=m, misfire_grace_time=15 * 60,
            id=f"{self.ecosystem.name}-health"
        )

    def _stop_scheduler(self) -> None:
        self.logger.info("Closing the tasks scheduler")
        scheduler = get_scheduler()
        scheduler.remove_job(f"{self.ecosystem.name}-health")
        self.logger.info("The tasks scheduler was closed properly")

    def analyse_picture(self) -> None:
        self.logger.info(f"Starting analysis of {self._ecosystem} image")
        # If got an image, analyse it
        if self._imageIO.getbuffer().nbytes:
            import random
            green = random.randrange(12000, 1500000, 1000)
            necrosis = random.uniform(5, 55)
            health_index = random.uniform(70, 97)
            self._plants_health = HealthRecord(
                timestamp=datetime.now().astimezone().replace(microsecond=0),
                green=green,
                necrosis=round(necrosis, 2),
                index=round(health_index, 2),
            )
            self.logger.info(f"{self._ecosystem} picture successfully analysed, "
                             f"indexes computed")
        else:
            # TODO: change Exception
            raise Exception

    def health_routine(self) -> None:
        # If webcam: turn it off and restart after
        light_running = self.ecosystem.get_subroutine_status("light")
        if light_running:
            light_subroutine: "Light" = self.ecosystem.subroutines["light"]
            light_mode = light_subroutine.actuator.mode
            light_status = light_subroutine.actuator.status
            light_subroutine.turn_light(ActuatorModePayload.on)
            self.take_picture()
            if light_mode is ActuatorMode.automatic:
                light_subroutine.turn_light(ActuatorModePayload.automatic)
            else:
                if light_status:
                    light_subroutine.turn_light(ActuatorModePayload.on)
                else:
                    light_subroutine.turn_light(ActuatorModePayload.off)
        else:
            self.take_picture()
        self.analyse_picture()

    def _update_manageable(self) -> None:
        def check_manageable() -> bool:
            cameras_uid = self.config.get_IO_group_uids("camera")
            if cameras_uid:
                for camera_uid in cameras_uid:
                    camera_dict = self.config.get_hardware_config(camera_uid)
                    measures = camera_dict.measures
                    if "health" in measures:
                        return True
            return False

        manageable = check_manageable()
        if manageable:
            self.manageable = True
        else:
            self.config.set_management("health", False)
            self.logger.warning(
                "No health camera detected, disabling Health subroutine"
            )
            self.manageable = False

    def _start(self) -> None:
        if not self.ecosystem.get_subroutine_status("light"):
            self.logger.warning(
                "The Ecosystem is not managing light subroutine, be sure the "
                "plants will receive sufficient and consistent light when "
                "taking the image."
            )

    def _stop(self) -> None:
        self.hardware = {}

    """API calls"""
    def get_hardware_needed_uid(self) -> set[str]:
        # TODO
        pass

    def take_picture(self) -> None:
        pass

    @property
    def plants_health(self) -> HealthRecord | Empty:
        return self._plants_health
