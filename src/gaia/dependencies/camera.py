import typing as t

_uninstalled_dependencies = False

try:
    import numpy as np
except ImportError:
    np = None
    _uninstalled_dependencies = True

try:
    import PIL
except ImportError:
    PIL = None
    _uninstalled_dependencies = True

try:
    from gaia_validators.image import Image
except ImportError:
    Image = None


if t.TYPE_CHECKING:
    import numpy as np
    import PIL

    from gaia_validators.image import Image


def check_dependencies() -> None:
    if _uninstalled_dependencies is True:
        raise RuntimeError(
            "All the dependencies required to use the camera have not been "
            "installed. Run 'pip install . [camera]' in your virtual "
            "environment to install them."
        )
