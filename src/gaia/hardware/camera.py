from __future__ import annotations

from datetime import datetime, timezone
from time import sleep
import typing as t
from typing import Type

from anyio.to_thread import run_sync

from gaia.dependencies.camera import PIL_image
from gaia.hardware.abc import Camera, hardware_logger
from gaia.hardware.utils import is_raspi


if t.TYPE_CHECKING:
    if is_raspi():  # pragma: no cover
        from picamera2 import Picamera2
    else:
        from gaia.hardware._compatibility import Picamera2


class PiCamera(Camera):
    def __del__(self) -> None:
        if hasattr(self, "_device") and self._device is not None:
            self._device.close()

    def _get_device(self) -> Picamera2:
        if is_raspi():  # pragma: no cover
            try:
                from picamera2 import Picamera2
            except ImportError:
                raise RuntimeError(
                    "picamera package is required. Run `pip install "
                    "picamera` in your virtual env."
                )
        else:
            from gaia.hardware._compatibility import Picamera2
        return Picamera2()

    async def get_image(self, size: tuple | None = None) -> PIL_image.Image:
        return await run_sync(self._get_image, size)

    def _get_image(self, size: tuple | None) -> PIL_image.Image:
        if size is not None:
            camera_config = self.device.create_still_configuration(main={"size": size})
        else:
            camera_config = self.device.create_still_configuration()
        self.device.configure(camera_config)
        self.device.start()
        # need at least 2 sec sleep for the camera to adapt to light level
        sleep(2)
        for retry in range(3):
            try:
                now = datetime.now(timezone.utc)
                array = self.device.capture_array("main")
                self.device.stop()
            except Exception as e:
                hardware_logger.error(
                    f"Camera {self._name} encountered an error. "
                    f"ERROR msg: `{e.__class__.__name__}: {e}`."
                )
            else:
                image: PIL_image.Image = PIL_image.fromarray(array)
                image.info["timestamp"] = now
                return image
        raise RuntimeError("There was an error while taking the picture.")


camera_models: dict[str, Type[Camera]] = {
    hardware.__name__: hardware for hardware in [
        PiCamera
    ]
}
