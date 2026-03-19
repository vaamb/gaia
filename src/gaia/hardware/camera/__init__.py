from __future__ import annotations

from datetime import datetime, timezone
from time import sleep
import typing as t
from typing import Any, Type

from anyio.to_thread import run_sync

from gaia.dependencies.camera import SerializableImage
from gaia.hardware.abc import Camera, PiCameraAddressMixin, hardware_logger
from gaia.hardware.utils import is_raspi


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.hardware.camera._devices._compatibility import Picamera2Device


class PiCamera(PiCameraAddressMixin, Camera):
    __slots__ = ()

    def __del__(self) -> None:
        if hasattr(self, "_device") and self._device is not None:
            self._device.close()

    def _get_device(self) -> Picamera2Device:
        if is_raspi():  # pragma: no cover
            try:
                from picamera2 import Picamera2 as Picamera2Device  # ty: ignore[unresolved-import]
            except ImportError:
                raise RuntimeError(
                    "picamera package is required. Run `pip install "
                    "picamera` in your virtual env."
                )
        else:
            from gaia.hardware.camera._devices._compatibility import Picamera2Device
        return Picamera2Device()

    async def get_image(self, size: tuple | None = None) -> SerializableImage:
        return await run_sync(self._get_image, size)

    def _get_image(self, size: tuple | None) -> SerializableImage:
        config: dict[str, Any] = {
            "format": "RGB888"
        }
        if size is not None:
            config.update({"size": size})
        camera_config = self.device.create_still_configuration(main=config)
        self.device.configure(camera_config)
        self.device.start()
        # need at least 2 sec sleep for the camera to adapt to light level
        if (
            self.ecosystem
            and not self.ecosystem.engine.config.app_config.TESTING
        ):
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
                image = SerializableImage(array, metadata={"timestamp": now})
                return image
        raise RuntimeError("There was an error while taking the picture.")


camera_models: dict[str, Type[Camera]] = {
    hardware.__name__: hardware
    for hardware in [
        PiCamera
    ]
}
