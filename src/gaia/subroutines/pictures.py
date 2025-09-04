from __future__ import annotations

import asyncio
from asyncio import Task
from math import ceil
from time import monotonic
from typing import TypedDict

# from anyio.to_process import run_sync as run_sync_in_process  # Crashes somehow
from anyio.to_thread import run_sync
from apscheduler.triggers.interval import IntervalTrigger

import gaia_validators as gv

from gaia.dependencies.camera import SerializableImage
from gaia.hardware import camera_models
from gaia.hardware.abc import Camera
from gaia.array_utils import (
    compute_mse, dump_picture_array, load_picture_array, rgb_to_gray)
from gaia.subroutines.template import SubroutineTemplate


class ScoredImage(TypedDict):
    image: SerializableImage | None
    score: float


_null_scored_image: ScoredImage = {
    "image": None,
    "score": -1.0,
}


class Pictures(SubroutineTemplate[Camera]):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hardware_choices = camera_models
        # Frequencies
        app_config = self.ecosystem.engine.config.app_config
        picture_period: float = app_config.PICTURE_TAKING_PERIOD
        sending_period: float = app_config.PICTURE_SENDING_PERIOD
        self._loop_period: float = float(picture_period)
        self._sending_ratio: int = ceil(sending_period / picture_period)
        self._sending_counter: int = 0
        self._picture_size: tuple[int, int] = app_config.PICTURE_SIZE
        self._picture_transfer_method: str = app_config.PICTURE_TRANSFER_METHOD
        if self._picture_transfer_method not in ("broker", "upload"):
            raise ValueError(
                f"Invalid picture transfer method: {self._picture_transfer_method}"
            )
        # Caching
        app_cache_dir = self.ecosystem.engine.config.cache_dir
        self._cache_dir = app_cache_dir / f"camera/{self.ecosystem.name}"
        if not self._cache_dir.exists():
            self._cache_dir.mkdir(parents=True)
        # Pictures
        self._sending_data_task: Task | None = None
        self._background_arrays: dict[str, SerializableImage] = {}
        self._scored_images: dict[str, ScoredImage] = {}
        self._finish__init__()

    async def _load_background_arrays(self) -> None:
        for camera_uid in self.hardware:
            array_path = self._cache_dir / f"{camera_uid}-background.pkl"
            if not array_path.exists():
                await self.reset_background_array(camera_uid)
            image = await run_sync(SerializableImage.load_array, array_path)
            self._background_arrays[camera_uid] = image

    async def _get_scored_image(self, camera: Camera) -> ScoredImage:
        image = await camera.get_image(size=self._picture_size)
        image.metadata["camera_uid"] = camera.uid
        background_array: SerializableImage = self._background_arrays[camera.uid]
        if self._sending_ratio > 1:
            gray_array = image.to_grayscale(inplace=False)
            mse = await run_sync(gray_array.compute_mse, background_array)
        else:
            mse = 1.0  # No need to compute it, the picture will be sent anyway
        return {
            "image": image,
            "score": mse,
        }

    async def update_scored_images(self) -> None:
        if not self.started:
            raise RuntimeError(
                "Picture subroutine has to be started to update the scored arrays"
            )
        for camera in self.hardware.values():
            new_scored_image = await self._get_scored_image(camera)
            old_scored_image = self._scored_images.get(camera.uid, _null_scored_image)
            if new_scored_image["score"] > old_scored_image["score"]:
                self._scored_images[camera.uid] = new_scored_image

    async def send_pictures(self) -> None:
        if self._picture_transfer_method == "broker":
            await self.ecosystem.engine.event_handler.send_picture_arrays(
                [self.ecosystem.uid])
        else:
            await self.ecosystem.engine.event_handler.upload_picture_arrays(
                [self.ecosystem.uid])

    async def send_pictures_if_possible(self) -> None:
        if (
                self._sending_data_task is None
                or self._sending_data_task.done()
                and self.ecosystem.engine.event_handler.is_connected()
        ):
            self._sending_data_task: Task = asyncio.create_task(
                self.send_pictures(), name=f"{self.ecosystem.uid}-picture-send_pictures")

    async def _routine(self) -> None:
        start_time: float = monotonic()
        try:
            await self.update_scored_images()
        except ValueError as e:
            self.logger.error(
                f"Encountered an error while updating scored arrays. "
                f"ERROR msg: `{e.__class__.__name__} :{e}`.")
        finally:
            update_time = monotonic() - start_time
            self.logger.debug(
                f"Pictures scored array update finished in {update_time:.1f} s.")
        if self._sending_counter % self._sending_ratio == 0:
            # Send data
            if self.ecosystem.engine.message_broker_started:
                try:
                    await self.send_pictures_if_possible()
                except Exception as e:
                    self.logger.error(
                        f"Encountered an error while sending picture arrays. "
                        f"ERROR msg: `{e.__class__.__name__} :{e}`."
                    )
                finally:
                    self._sending_counter = 0
            # Reset scores
            for scored_array in self._scored_images.values():
                scored_array["score"] = -1.0
        self._sending_counter += 1
        loop_time = monotonic() - start_time
        if loop_time > self._loop_period:  # pragma: no cover
            self.logger.warning(
                f"Pictures routine took {loop_time:.1f} s. This either "
                f"indicates errors while getting pictures and computing mse or "
                f"that the computing power requested to analyse the pictures is "
                f"too big. You might need to adapt 'PICTURE_TAKING_PERIOD' or "
                f"'PICTURE_SIZE'.")

    def _compute_if_manageable(self) -> bool:
        if not self.ecosystem.get_hardware_group_uids(gv.HardwareType.camera):
            self.logger.warning("No Camera detected, disabling Picture subroutine.")
            return False
        if not self.ecosystem.engine.message_broker_started:
            self.logger.warning(
                "The engine is not using event dispatcher, the photo taken"
                "will not be sent to Ouranos.")
        return True

    async def _start(self) -> None:
        self.logger.info(
            f"Starting the picture loop. It will run every "
            f"{self._loop_period:.1f} s and send picture every "
            f"{self._sending_ratio} iteration(s).")
        self.logger.info("Loading background image(s).")
        await self._load_background_arrays()
        self.ecosystem.engine.scheduler.add_job(
            func=self.routine,
            id=f"{self.ecosystem.uid}-picture_routine",
            trigger=IntervalTrigger(
                seconds=self._loop_period,
                jitter=self._loop_period / 10,
            ),
        )
        self.logger.debug("Picture loop successfully started.")

    async def _stop(self) -> None:
        self.logger.info("Stopping picture loop.")
        self.ecosystem.engine.scheduler.remove_job(
            f"{self.ecosystem.uid}-picture_routine")
        self._sending_data_task = None

    """API calls"""
    # Picture
    @property
    def picture_arrays(self) -> list[SerializableImage]:
        return [
            scored_array["image"]
            for scored_array in self._scored_images.values()
            if scored_array["image"] is not None
        ]

    def get_hardware_needed_uid(self) -> set[str]:
        return set(self.ecosystem.get_hardware_group_uids(gv.HardwareType.camera))

    async def refresh(self) -> None:
        await super().refresh()
        await self._load_background_arrays()

    async def reset_background_array(self, camera_uid: str) -> None:
        array_path = self._cache_dir / f"{camera_uid}-background.pkl"
        camera = self.hardware[camera_uid]
        image: SerializableImage = await camera.get_image()
        image.to_grayscale(inplace=True)
        await run_sync(image.dump_array, array_path)

    async def reset_background_arrays(self) -> None:
        for camera_uid in self.hardware:
            try:
                await self.reset_background_array(camera_uid)
            except RuntimeError:
                self.logger.error(
                    f"Could not reset background image for the camera with the "
                    f"uid '{camera_uid}'.")
                raise
