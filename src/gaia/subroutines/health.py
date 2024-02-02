from __future__ import annotations

from datetime import datetime
import io
import typing as t

import gaia_validators as gv

from gaia.dependencies import check_dependencies
from gaia.hardware import camera_models
from gaia.hardware.abc import Camera
from gaia.subroutines.template import SubroutineTemplate


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.subroutines.light import Light


class Health(SubroutineTemplate):
    # TODO: fix
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hardware_choices = camera_models
        self.hardware: dict[str, Camera]
        self._plants_health: gv.HealthRecord | gv.Empty = gv.Empty()
        self._imageIO = io.BytesIO()
        self._finish__init__()

    def _start_scheduler(self) -> None:
        h, m = self.ecosystem.engine.config.app_config.HEALTH_LOGGING_TIME.split("h")
        self.ecosystem.engine.scheduler.add_job(
            self.health_routine,
            trigger="cron", hour=h, minute=m, misfire_grace_time=15 * 60,
            id=f"{self.ecosystem.name}-health"
        )

    def _stop_scheduler(self) -> None:
        self.logger.info("Closing the tasks scheduler")
        self.ecosystem.engine.scheduler.remove_job(f"{self.ecosystem.name}-health")
        self.logger.info("The tasks scheduler was closed properly")

    def analyse_picture(self) -> None:
        self.logger.info(f"Starting analysis of {self._ecosystem} image")
        # If got an image, analyse it
        if self._imageIO.getbuffer().nbytes:
            import random
            green = random.randrange(12000, 1500000, 1000)
            necrosis = random.uniform(5, 55)
            health_index = random.uniform(70, 97)
            self._plants_health = gv.HealthRecord(
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
            light_mode = light_subroutine.actuator_handler.mode
            light_status = light_subroutine.actuator_handler.status
            light_subroutine.turn_light(gv.ActuatorModePayload.on)
            self.take_picture()
            if light_mode is gv.ActuatorMode.automatic:
                light_subroutine.turn_light(gv.ActuatorModePayload.automatic)
            else:
                if light_status:
                    light_subroutine.turn_light(gv.ActuatorModePayload.on)
                else:
                    light_subroutine.turn_light(gv.ActuatorModePayload.off)
        else:
            self.take_picture()
        self.analyse_picture()

    def _compute_if_manageable(self) -> bool:
        cameras_uid = self.config.get_IO_group_uids(gv.HardwareType.camera)
        if cameras_uid:
            for camera_uid in cameras_uid:
                camera_dict = self.config.get_hardware_config(camera_uid)
                measures = camera_dict.measures
                if "health" in measures:
                    return True
        self.logger.warning("No health camera detected.")
        return False

    def _start(self) -> None:
        check_dependencies("camera")
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
    def plants_health(self) -> gv.HealthRecord | gv.Empty:
        return self._plants_health
