import typing as t

_uninstalled_dependencies = False

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None
    _uninstalled_dependencies = True

try:
    import PIL
    from PIL import Image as PIL_image

except ImportError:  # pragma: no cover

    class _Image:
        Image = None

    PIL = None
    PIL_image = _Image()
    _uninstalled_dependencies = True

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None
#    _uninstalled_dependencies = True


try:
    from gaia_validators.image import SerializableImage, SerializableImagePayload
except ImportError:  # pragma: no cover
    SerializableImage = None
    SerializableImagePayload = None
    _uninstalled_dependencies = True


if t.TYPE_CHECKING:  # pragma: no cover
    import numpy as np
    import PIL
    from PIL import Image as PIL_image
    import cv2

    from gaia_validators.image import SerializableImage, SerializableImagePayload


def check_dependencies(check_cv2: bool = True) -> None:
    if check_cv2:
        if _uninstalled_dependencies is True or cv2 is None:  # pragma: no cover
            raise RuntimeError(
                "All the dependencies required to use the camera have not been "
                "installed. Run 'pip install . [camera]' in your virtual "
                "environment to install them."
            )
    else:
        if _uninstalled_dependencies is True:  # pragma: no cover
            raise RuntimeError(
                "All the dependencies required to use the camera have not been "
                "installed. Run 'pip install . [camera]' in your virtual "
                "environment to install them."
            )
