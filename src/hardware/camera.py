from datetime import datetime
from pathlib import Path
from time import sleep
import typing as t

from . import _IS_RASPI
from .ABC import Camera


if t.TYPE_CHECKING:
    if _IS_RASPI:  # pragma: no cover
        from picamera import PiCamera as _PiCamera
    else:
        from ._compatibility import PiCamera as _PiCamera


class PiCamera(Camera):
    def _get_camera(self) -> "_PiCamera":
        if _IS_RASPI:  # pragma: no cover
            try:
                from picamera import PiCamera as _PiCamera
            except ImportError:
                raise RuntimeError(
                    "picamera package is required. Run `pip install "
                    "picamera` in your virtual env."
                )
        else:
            from ._compatibility import PiCamera as _PiCamera
        return _PiCamera

    def take_picture(self) -> Path:
        with self._get_camera() as camera:
            camera.resolution = (3280, 2464)
            camera.start_preview()
            # need at least 2 sec sleep for the camera to adapt to light level
            sleep(3)
            current_datetime = datetime.now().strftime("%Y.%m.%d:%H.%M.%S")
            picture_name = f"{self.ecosystem_uid}-{current_datetime}"
            picture_path = self.cam_dir / picture_name
            camera.capture(picture_path, format="jpg")
        return picture_path

    def take_video(self):
        pass
        # yield
