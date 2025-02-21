from __future__ import annotations

import asyncio
from asyncio import Task
from datetime import datetime, timezone
from time import monotonic
import typing as t
from typing import Any, Type, TypedDict

from anyio.to_thread import run_sync
from apscheduler.triggers.cron import CronTrigger
import numpy as np

import gaia_validators as gv
from numpy import floating

from gaia.dependencies.camera import check_dependencies, SerializableImage
from gaia.hardware import camera_models
from gaia.hardware.abc import Camera, Measure
from gaia.subroutines.template import SubroutineTemplate


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.database.models import HealthBuffer, HealthRecord
    from gaia.subroutines.light import Light


indices: dict[Measure, str] = {
    Measure.mpri: "(g-r)/(g+r)",  # Yang, Willis & Mueller 2008
    Measure.ndrgi: "(r-g)/(g+r)",  # Yang, Willis & Mueller 2008
    Measure.ndvi: "(nir-r)/(nir+r)",
    Measure.vari: "(g-r)/(g+r-b)",
}


class _PartialHealthRecord(TypedDict):
    measure: str
    value: float


class Health(SubroutineTemplate):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hardware_choices = camera_models
        self.hardware: dict[str, Camera]
        # Picture size
        app_config = self.ecosystem.engine.config.app_config
        self._picture_size: tuple[int, int] = app_config.PICTURE_SIZE
        # Records
        self._sending_data_task: Task | None = None
        self._plants_health: gv.HealthData | gv.Empty = gv.Empty()
        # self._data_lock = Lock()
        self._finish__init__()

    """SubroutineTemplate methods"""
    async def _routine(self) -> None:
        start_time = monotonic()
        self.logger.debug("Starting health data update routine ...")
        try:
            await self.update_health_data()
        except Exception as e:
            self.logger.error(
                f"Encountered an error while updating health data. "
                f"ERROR msg: `{e.__class__.__name__} :{e}`."
            )
        finally:
            update_time = monotonic() - start_time
            self.logger.debug(f"Health data update finished in {update_time:.1f} s.")
        if self.ecosystem.engine.use_message_broker:
            try:
                await self.schedule_send_data()
            except Exception as e:
                self.logger.error(
                    f"Encountered an error while sending health data. "
                    f"ERROR msg: `{e.__class__.__name__} :{e}`."
                )
        routine_time = monotonic() - start_time
        self.logger.debug(f"Health routine took {routine_time:.1f} s.")

    def _compute_if_manageable(self) -> bool:
        try:
            check_dependencies()
        except RuntimeError:
            self.logger.warning(
                "Health subroutine does not have all the dependencies installed.")
            return False
        cameras_uid = self.config.get_IO_group_uids(gv.HardwareType.camera)
        for camera_uid in cameras_uid:
            camera_dict = self.config.get_hardware_config(camera_uid)
            measures_name = [measure.name for measure in camera_dict.measures]
            indices_name = [index.value for index in indices.keys()]
            if any(measure in indices_name for measure in measures_name):
                return True
        self.logger.warning("No health camera detected.")
        return False

    async def _start(self) -> None:
        h, m = self.ecosystem.engine.config.app_config.HEALTH_LOGGING_TIME.split("h")
        self.logger.info(
            f"Starting the health subroutine. It will run every day at {h}h{m}.")
        if not self.ecosystem.get_subroutine_status("light"):
            self.logger.warning(
                f"{self.ecosystem.name} is not managing light subroutine, be "
                f"sure the ecosystem receive sufficient and consistent light "
                f"when the subroutine is run.")
        self.ecosystem.engine.scheduler.add_job(
            func=self.routine,
            id=f"{self.ecosystem.uid}-health_routine",
            trigger=CronTrigger(hour=h, minute=m, jitter=5.0),
            misfire_grace_time=15 * 60,
        )

    async def _stop(self) -> None:
        self.logger.info("Stopping health subroutine.")
        self.ecosystem.engine.scheduler.remove_job(
            f"{self.ecosystem.uid}-health_routine")
        self._sending_data_task = None

    def get_hardware_needed_uid(self) -> set[str]:
        return set(self.config.get_IO_group_uids(IO_type=gv.HardwareType.camera))

    """Routine specific methods"""
    @property
    def plants_health(self) -> gv.HealthData | gv.Empty:
        #async with self._data_lock:
        return self._plants_health

    @plants_health.setter
    def plants_health(self, data: gv.HealthData | gv.Empty) -> None:
        #async with self._data_lock:
        self._plants_health = data

    async def _get_the_images(self) -> dict[str, SerializableImage]:
        """Get the images from the cameras before analysing them as depending
        on the config, lights can be set on while taking the images, which can
        disturb animals.
        """
        # If webcam: turn it off and restart after
        light_subroutine: Light = self.ecosystem.subroutines["light"]
        light_mode = light_subroutine.actuator_handler.mode
        light_status = light_subroutine.actuator_handler.status
        # Turn the lights on
        if light_subroutine.started:
            self.logger.info("Turning on the light(s) to take a 'health' picture.")
            await light_subroutine.turn_light(gv.ActuatorModePayload.on)
        # Get the pictures
        images: dict[str, SerializableImage] = {}
        for camera_uid, camera in self.hardware.items():
            camera: Camera
            images[camera_uid] = await camera.get_image(size=self._picture_size)
        # Turn the lights back to their previous state
        if light_subroutine.started:
            self.logger.info("Turning back the light subroutine to its previous state.")
            if light_mode is gv.ActuatorMode.automatic:
                await light_subroutine.turn_light(gv.ActuatorModePayload.automatic)
            else:
                if light_status:
                    await light_subroutine.turn_light(gv.ActuatorModePayload.on)
                else:
                    await light_subroutine.turn_light(gv.ActuatorModePayload.off)
        # return the images
        return images

    @staticmethod
    def _get_index(image0: SerializableImage, measure: Measure) -> floating[Any]:
        image1 = image0.apply_rgb_formula(indices[measure])
        return np.mean(image1.array)

    @staticmethod
    async def _get_partial_record(
            image0: SerializableImage,
            measure: Measure,
    ) -> _PartialHealthRecord:
        index = await run_sync(Health._get_index, image0, measure)
        return {
            "measure": measure.value,
            "value": index,
        }

    async def _get_records_for_image(
            self,
            camera_uid: str,
            image: SerializableImage,
    ) -> list[gv.HealthRecord]:
        camera: Camera = self.hardware[camera_uid]
        measures = camera.measures
        now = datetime.now().astimezone(timezone.utc).replace(microsecond=0)
        futures = [
            asyncio.create_task(self._get_partial_record(image, measure))
            for measure in measures
        ]
        partial_records = await asyncio.gather(*futures)
        return [
            gv.HealthRecord(
                sensor_uid=camera_uid,
                measure=record["measure"],
                value=record["value"],
                timestamp=now,
            )
            for record in partial_records
        ]

    async def _analyse_images(
            self,
            images: dict[str, SerializableImage],
    ) -> list[gv.HealthRecord] | gv.Empty:
        self.logger.debug(f"Starting analysis of {self._ecosystem.name} image(s).")
        rv: list[gv.HealthRecord] = []
        futures = [
            asyncio.create_task(self._get_records_for_image(camera_uid, image))
            for camera_uid, image in images.items()
        ]
        for records in await asyncio.gather(*futures):
            rv.extend(records)
        self.logger.debug(f"Analysis of {self._ecosystem.name} image(s) done.")
        return rv

    async def update_health_data(self) -> None:
        if not self.started:
            raise RuntimeError(
                "Health subroutine has to be started to update the health data"
            )
        self.logger.info("Getting the images.")
        images = await self._get_the_images()
        timestamp = datetime.now(timezone.utc).replace(microsecond=0)
        if images:
            self.logger.info("Analyzing the images.")
            self.plants_health = {
                "timestamp": timestamp,
                "records": await self._analyse_images(images),
            }
        else:
            self.plants_health = gv.Empty()

    async def _log_data(self, db_model: Type[HealthBuffer | HealthRecord]) -> None:
        async with self.ecosystem.engine.db.scoped_session() as session:
            for record in self._plants_health:
                session.add(
                    db_model(
                        ecosystem_uid=self.ecosystem.uid,
                        sensor_uid=record.sensor_uid,
                        measure=record.measure,
                        value=record.value,
                        timestamp=record.timestamp,
                    )
                )
            await session.commit()

    async def log_data(self) -> None:
        if not self.ecosystem.engine.use_db:
            return

        from gaia.database.models import HealthRecord

        await self._log_data(HealthRecord)

    async def send_data(self) -> None:
        # Check if we use the message broker
        if not self.ecosystem.engine.use_message_broker:
            return

        sent: bool = False
        try:
            # Can be cancelled if it takes too long
            if self.ecosystem.event_handler.is_connected():
                sent = await self.ecosystem.engine.event_handler.send_payload(
                    "health_data", ecosystem_uids=[self.ecosystem.uid])

        finally:
            if not sent and self.ecosystem.engine.use_db:
                from gaia.database.models import HealthBuffer

                await self._log_data(HealthBuffer)

    async def schedule_send_data(self) -> None:
        if not(
                self._sending_data_task is None
                or self._sending_data_task.done()
        ):
            self.logger.warning(
                "There is already an health data sending task running. It will "
                "be cancelled to start a new one."
            )
            self._sending_data_task.cancel()
        self._sending_data_task = asyncio.create_task(
            self.send_data(), name=f"{self.ecosystem.uid}-health-send_data")
