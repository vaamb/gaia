from datetime import datetime
from pathlib import Path
from time import sleep

from .ABC import Camera, _RASPI


if _RASPI:  # pragma: no cover
    from picamera import PiCamera as _PiCamera
else:
    from .compatibility import PiCamera as _PiCamera


class PiCamera(Camera):
    def take_picture(self) -> Path:

        with _PiCamera() as camera:
            camera.resolution = (3280, 2464)
            camera.start_preview()
            # need at least 2 sec sleep for the camera to adapt to light level
            sleep(3)
            current_datetime = datetime.now().strftime("%Y.%m.%d:%H.%M.%S")
            picture_name = f"{self.ecosystem_uid}-{current_datetime}"
            picture_path = self.folder/picture_name
            camera.capture(picture_path, format="jpg")
        return picture_path

    def take_video(self):
        pass
        # yield
