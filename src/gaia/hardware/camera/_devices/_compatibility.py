from typing import Any


class Picamera2Device:
    def __init__(self):
        self._cfg: dict = {"size": (800, 600)}

    def create_preview_configuration(self, main={}, *args, **kwargs) -> dict:
        return {"size": (800, 600), **main}

    def create_still_configuration(self, main={}, *args, **kwargs) -> dict:
        return {"size": (800, 600), **main}

    def create_video_configuration(self, main={}, *args, **kwargs) -> dict:
        return {"size": (800, 600), **main}

    def capture_array(self, name="main") -> Any:
        import numpy as np

        width, height = self._cfg["size"]
        array = np.stack(
            (
                np.random.binomial(255, 0.639, (height, width)).astype("uint8"),  #b
                np.random.binomial(255, 0.420, (height, width)).astype("uint8"),  #g
                np.random.binomial(255, 0.133, (height, width)).astype("uint8"),  #r
            ),
            axis=2,
        )
        return array

    def configure(self, camera_config: dict | str) -> None:
        if isinstance(camera_config, dict):
            self._cfg.update(camera_config)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def capture_file(self, name: str, format: str = "jpg") -> None:
        pass

    def close(self) -> None:
        pass
