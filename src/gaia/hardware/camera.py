from __future__ import annotations

from datetime import datetime
from time import sleep
import typing as t
from typing import Type, Generator

from gaia.hardware.abc import Camera, Image
from gaia.hardware.utils import _IS_RASPI


if t.TYPE_CHECKING:
    if _IS_RASPI:  # pragma: no cover
        from picamera2 import PiCamera2 as _PiCamera, Preview
    else:
        from gaia.hardware._compatibility import PiCamera as _PiCamera, Preview


class PiCamera(Camera):
    def _get_device(self) -> "_PiCamera":
        if _IS_RASPI:  # pragma: no cover
            try:
                from picamera import PiCamera as _PiCamera, Preview
            except ImportError:
                raise RuntimeError(
                    "picamera package is required. Run `pip install "
                    "picamera` in your virtual env."
                )
        else:
            from gaia.hardware._compatibility import PiCamera as _PiCamera, Preview
        return _PiCamera()

    def get_image(self) -> Image | None:
        camera_config = self.device.create_still_configuration()
        self.device.configure(camera_config)
        self.device.start_preview(Preview.QTGL)
        self.device.start()
        # need at least 2 sec sleep for the camera to adapt to light level
        sleep(2)
        for retry in range(3):
            try:
                now = datetime.now().astimezone()
                array = self.device.capture_array("main")
                self.device.stop()
                return Image(array=array, timestamp=now)
            except Exception as e:
                print(e)

    def get_timelapse(
            self,
            frequency: int | float = 0.5
    ) -> Generator[Image, None, None]:
        if frequency > 2.0:
            frequency = 2.0
        camera_config = self.device.create_still_configuration()
        self.device.configure(camera_config)
        self.device.start_preview(Preview.QTGL)
        self.device.start()
        sleep(2)
        try:
            while True:
                now = datetime.now().astimezone()
                array = self.device.capture_array("main")
                yield Image(array=array, timestamp=now)
                sleep(1/frequency)
        finally:
            self.device.stop()


camera_models: dict[str, Type[Camera]] = {
    hardware.__name__: hardware for hardware in [
        PiCamera
    ]
}
