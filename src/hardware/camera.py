from datetime import datetime
import os
from time import sleep

from .base import Camera
from src.utils import base_dir


class cameraModule:
    def __init__(self, ecosystem_name: str) -> None:
        self.ecosystem_name = ecosystem_name
        self._camera_folder = base_dir/"camera"
        if not self._camera_folder.exists():
            os.mkdir(self._camera_folder)

    def take_picture(self):
        with Camera() as camera:
            camera.resolution = (3280, 2464)
            camera.start_preview()
            # need at least 2 sec sleep for the camera to adapt to light level
            sleep(5)
            current_datetime = datetime.now().strftime("%Y.%m.%d-%H.%M.%S")
            pic_name = f"{self.ecosystem_name}-{current_datetime}"
            # pic_path = self._camera_folder/pic_name
            # camera.capture(pic_path, format="png")

    def take_video(self):
        pass
