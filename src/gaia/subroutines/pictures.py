from __future__ import annotations

import asyncio
from asyncio import Task
from datetime import datetime
from math import ceil
from time import monotonic
from typing import TypedDict

# from anyio.to_process import run_sync as run_sync_in_process  # Crashes somehow
from anyio.to_thread import run_sync
from apscheduler.triggers.interval import IntervalTrigger

import gaia_validators as gv

from gaia.dependencies.camera import np, SerializableImage
from gaia.hardware import camera_models
from gaia.hardware.abc import Camera
from gaia.array_utils import (
    compute_mse, dump_picture_array, load_picture_array, rgb_to_gray)
from gaia.subroutines.template import SubroutineTemplate


class ScoredArray(TypedDict):
    array: np.ndarray | None
    score: float
    timestamp: datetime


_null_scored_array: ScoredArray = {
    "array": None,
    "score": -1.0,
    "timestamp": datetime.fromtimestamp(0)
}


class Pictures(SubroutineTemplate):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hardware_choices = camera_models
        self.hardware: dict[str, Camera]
        # Frequencies
        app_config = self.ecosystem.engine.config.app_config
        picture_period: int = app_config.PICTURE_TAKING_PERIOD
        sending_period: int = app_config.PICTURE_SENDING_PERIOD
        self._loop_period: float = float(picture_period)
        self._sending_ratio: int = ceil(sending_period / picture_period)
        self._sending_counter: int = 0
        self._picture_size: tuple[int, int] = app_config.PICTURE_SIZE
        # Caching
        app_cache_dir = self.ecosystem.engine.config.cache_dir
        self._cache_dir = app_cache_dir / f"camera/{self.ecosystem.name}"
        if not self._cache_dir.exists():
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        # Pictures
        self._sending_data_task: Task | None = None
        self._background_arrays: dict[str, np.ndarray] = {}
        self._scored_arrays: dict[str, ScoredArray] = {}
        self._finish__init__()

    async def _load_background_arrays(self) -> None:
        for camera_uid in self.hardware:
            array_path = self._cache_dir / f"{camera_uid}-background.pkl"
            if array_path.exists():
                array = await run_sync(load_picture_array, array_path)
            else:
                camera: Camera = self.hardware[camera_uid]
                image = await camera.get_image(size=self._picture_size)
                array = np.array(image)
                if self._sending_ratio > 1:
                    array = rgb_to_gray(array)
                await run_sync(dump_picture_array, array, array_path)
            self._background_arrays[camera_uid] = array

    async def _get_scored_array(self, camera: Camera) -> ScoredArray:
        image = await camera.get_image(size=self._picture_size)
        array = np.array(image)
        timestamp: datetime = image.info.get("timestamp")
        background_array: np.ndarray = self._background_arrays[camera.uid]
        if self._sending_ratio > 1:
            gray_array = rgb_to_gray(array)
            mse = await run_sync(compute_mse, background_array, gray_array)
        else:
            mse = 1.0  # No need to compute it, the picture will be sent anyway
        return {
            "array": array,
            "score": mse,
            "timestamp": timestamp,
        }

    async def update_scored_arrays(self) -> None:
        if not self.started:
            raise RuntimeError(
                "Picture subroutine has to be started to update the scored arrays"
            )
        for camera in self.hardware.values():
            new_scored_array = await self._get_scored_array(camera)
            old_scored_array = self._scored_arrays.get(camera.uid, _null_scored_array)
            if new_scored_array["score"] > old_scored_array["score"]:
                self._scored_arrays[camera.uid] = new_scored_array

    async def send_pictures(self) -> None:
        await self.ecosystem.engine.event_handler.send_picture_arrays(
            [self.ecosystem.uid])

    async def send_pictures_if_possible(self) -> None:
        if (
                self._sending_data_task is None
                or self._sending_data_task.done()
                and self.ecosystem.engine.event_handler.is_connected()
        ):
            self._sending_data_task: Task = asyncio.create_task(
                self.send_pictures(), name=f"{self.ecosystem.uid}-picture-send_pictures")

    async def routine(self) -> None:
        start_time: float = monotonic()
        self.logger.debug("Starting picture routine ...")
        try:
            await self.update_scored_arrays()
        except ValueError as e:
            self.logger.error(
                f"Encountered an error while updating scored arrays. "
                f"ERROR msg: `{e.__class__.__name__} :{e}`.")
        finally:
            update_time = monotonic() - start_time
            self.logger.debug(
                f"Picture scored array update finished in {update_time:.1f} s.")
        if self._sending_counter % self._sending_ratio == 0:
            # Send data
            if self.ecosystem.engine.use_message_broker:
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
            for scored_array in self._scored_arrays.values():
                scored_array["score"] = -1.0
        self._sending_counter += 1
        loop_time = monotonic() - start_time
        if loop_time > self._loop_period:  # pragma: no cover
            self.logger.warning(
                f"Picture routine took {loop_time:.1f} s. This either "
                f"indicates errors while getting pictures and computing mse or "
                f"that the computing power requested to analyse the pictures is "
                f"too big. You might need to adapt 'PICTURE_TAKING_PERIOD' or "
                f"'PICTURE_SIZE'.")
        self.logger.debug(
            f"Picture routine finished in {loop_time:.1f} s.")

    def _compute_if_manageable(self) -> bool:
        if not self.config.get_IO_group_uids(gv.HardwareType.camera):
            self.logger.warning(
                "No Camera detected, disabling Picture subroutine.")
            return False
        if not self.ecosystem.engine.use_message_broker:
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
            trigger=IntervalTrigger(seconds=self._loop_period, jitter=self._loop_period / 10),
        )
        self.logger.debug(f"Picture loop successfully started.")

    async def _stop(self) -> None:
        self.logger.info(f"Stopping picture loop")
        self.ecosystem.engine.scheduler.remove_job(
            f"{self.ecosystem.uid}-picture_routine")
        self._sending_data_task = None

    """API calls"""
    # Picture
    @property
    def picture_arrays(self) -> list[SerializableImage]:
        return [
            SerializableImage.from_array(
                array=scored_array["array"],
                metadata={
                    "camera_uid": camera_uid,
                    "timestamp": scored_array["timestamp"],
                }
            )
            for camera_uid, scored_array in self._scored_arrays.items()
            if scored_array["array"] is not None
        ]

    def get_hardware_needed_uid(self) -> set[str]:
        return set(self.config.get_IO_group_uids(IO_type=gv.HardwareType.camera))

    async def refresh_hardware(self) -> None:
        await super().refresh_hardware()
        await self._load_background_arrays()

    async def reset_background_array(self, camera_uid: str) -> None:
        array_path = self._cache_dir / f"{camera_uid}-background.pkl"
        camera: Camera = self.hardware[camera_uid]
        image = await camera.get_image()
        array = np.array(image)
        await run_sync(dump_picture_array, array, array_path)

    async def reset_background_arrays(self) -> None:
        for camera_uid in self.hardware:
            try:
                await self.reset_background_array(camera_uid)
            except RuntimeError:
                self.logger.error(
                    f"Could not reset background image for the camera with the "
                    f"uid '{camera_uid}'.")
                raise
