import typing as t

_uninstalled_dependencies = False

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None
    _uninstalled_dependencies = True

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None
    _uninstalled_dependencies = True


try:
    from gaia_validators.image import SerializableImage, SerializableImagePayload
except ImportError:  # pragma: no cover
    SerializableImage = None
    SerializableImagePayload = None
    _uninstalled_dependencies = True


if t.TYPE_CHECKING:  # pragma: no cover
    import numpy as np
    import cv2

    from gaia_validators.image import SerializableImage, SerializableImagePayload


def check_dependencies(check_cv2: bool = True) -> None:
    if _uninstalled_dependencies is True:  # pragma: no cover
        raise RuntimeError(
            "All the dependencies required to use the camera have not been "
            "installed. Run 'pip install . [camera]' in your virtual "
            "environment to install them."
        )
