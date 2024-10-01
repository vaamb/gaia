from __future__ import annotations

from datetime import datetime, timezone
from time import sleep
import typing as t
from typing import Type

from anyio.to_thread import run_sync

from gaia.hardware.abc import Camera, hardware_logger, Image
from gaia.hardware.utils import is_raspi


if t.TYPE_CHECKING:
    if is_raspi():  # pragma: no cover
        from picamera2 import Picamera2 as _PiCamera
    else:
        from gaia.hardware._compatibility import Picamera2 as _PiCamera


class PiCamera(Camera):
    def _get_device(self) -> "_PiCamera":
        if is_raspi():  # pragma: no cover
            try:
                from picamera2 import Picamera2 as _PiCamera
            except ImportError:
                raise RuntimeError(
                    "picamera package is required. Run `pip install "
                    "picamera` in your virtual env."
                )
        else:
            from gaia.hardware._compatibility import Picamera2 as _PiCamera
        return _PiCamera()

    async def get_image(self) -> Image | None:
        return await run_sync(self._get_image)

    def _get_image(self) -> Image | None:
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
                    f"ERROR msg: `{e.__class__.__name__}: {e}`"
                )
            else:
                return Image.from_array(array=array, metadata={"timestamp": now})
        return None


camera_models: dict[str, Type[Camera]] = {
    hardware.__name__: hardware for hardware in [
        PiCamera
    ]
}
