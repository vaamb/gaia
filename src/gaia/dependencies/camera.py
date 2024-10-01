import typing as t

_uninstalled_dependencies = False

try:
    import numpy as np
except ImportError:
    np = None
    _uninstalled_dependencies = True

try:
    import PIL
    from PIL import Image as PIL_image

except ImportError:
    class _Image:
        Image = None

    PIL = None
    PIL_image = _Image()
    _uninstalled_dependencies = True


if t.TYPE_CHECKING:
    import numpy as np
    import PIL
    from PIL import Image as PIL_image


def check_dependencies() -> None:
    if _uninstalled_dependencies is True:
        raise RuntimeError(
            "All the dependencies required to use the camera have not been "
            "installed. Run 'pip install . [camera]' in your virtual "
            "environment to install them."
        )
