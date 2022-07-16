import datetime
import io
import typing as t

from ..shared_resources import scheduler
from ..subroutines.template import SubroutineTemplate
from config import Config


if t.TYPE_CHECKING:  # pragma: no cover
    from .light import Light


class Health(SubroutineTemplate):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._plants_health = {}
        self._imageIO = io.BytesIO

        self._finish__init__()

    def _start_scheduler(self):
        h, m = Config.HEALTH_LOGGING_TIME.split("h")
        scheduler.add_job(
            self._health_routine,
            trigger="cron", hour=h, minute=m, misfire_grace_time=15 * 60,
            id=f"{self._ecosystem_name}-health"
        )

    def _stop_scheduler(self):
        self.logger.info("Closing the tasks scheduler")
        scheduler.remove_job(f"{self._ecosystem_name}-health")
        self.logger.info("The tasks scheduler was closed properly")

    def _health_routine(self):
        # If webcam: turn it off and restart after
        light_subroutine: "Light" = self.ecosystem.subroutines["light"]
        light_mode = light_subroutine.mode
        light_status = light_subroutine.light_status
        # webcam = self.
        if light_subroutine.status:
            light_subroutine.turn_light("on")
        try:
            self.logger.info(f"Taking picture of {self._ecosystem_name}")
            self.take_picture()
            self.logger.debug(
                f"Picture of {self._ecosystem_name} successfully taken")
        except Exception as e:
            self.logger.error(
                f"Failed to take picture of {self._ecosystem_name}. "
                f"ERROR msg: `{e.__class__.__name__}: {e}`."
            )

        if light_subroutine.status:
            if light_mode == "automatic":
                light_subroutine.turn_light("automatic")
            else:
                if light_status:
                    light_subroutine.turn_light("on")
                else:
                    light_subroutine.turn_light("off")

        self.analyse_image()

    def _update_manageable(self) -> None:
        if self.config.get_IO_group("camera"):
            self.manageable = True
        else:
            self.config.set_management("health", False)
            self.logger.warning(
                "No camera detected, disabling Health subroutine"
            )
            self.manageable = False

    def _start(self):
        if not self.ecosystem.subroutines["light"].started:
            self.logger.warning(
                "The Ecosystem is not managing light subroutine, be sure the "
                "plants will receive sufficient and consistent light when "
                "taking the image."
            )

    def _stop(self):
        self.hardware = {}

    """API calls"""
    def add_hardware(self, hardware_dict: dict):
        pass

    def remove_hardware(self, hardware_uid: str) -> None:
        pass

    def refresh_hardware(self) -> None:
        pass

    def take_picture(self):
        pass

    def analyse_image(self):
        self.logger.info(f"Starting analysis of {self._ecosystem} image")
        # If got an image, analyse it
        if self._imageIO.getbuffer().nbytes:
            import random
            green = random.randrange(12000, 1500000, 1000)
            necrosis = random.uniform(5, 55)
            health_index = random.uniform(70, 97)
            self._plants_health = {
                "datetime": datetime.datetime.now().replace(microsecond=0),
                "data": {
                    "green": green,
                    "necrosis": round(necrosis, 2),
                    "index": round(health_index, 2),
                },
            }
            self.logger.info(f"{self._ecosystem} picture successfully analysed, "
                             f"indexes computed")
        else:
            # TODO: change Exception
            raise Exception

    @property
    def plants_health(self):
        return self._plants_health
