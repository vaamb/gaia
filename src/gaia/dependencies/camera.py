from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec
import typing as t


_lazy_objects: dict[str, tuple[str, str | None]] = {
    "np": ("numpy", None),
    "cv2": ("cv2", None),
    "SerializableImage": ("gaia_validators.image", "SerializableImage"),
    "SerializableImagePayload": ("gaia_validators.image", "SerializableImagePayload"),
}

_missing_dependencies_msg = (
    "All the dependencies required to use the camera have not been "
    "installed. Run `uv sync --inexact --extra camera` in your virtual "
    "environment to install them."
)


def _is_available(module_name: str) -> bool:
    try:
        return find_spec(module_name) is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


def check_dependencies(check_cv2: bool = True) -> None:
    for module_name in ("numpy", "cv2", "gaia_validators.image"):
        if not _is_available(module_name):  # pragma: no cover
            raise RuntimeError(_missing_dependencies_msg)


def __getattr__(name: str) -> t.Any:
    try:
        module_name, attribute = _lazy_objects[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    try:
        module = import_module(module_name)
    except ImportError:  # pragma: no cover
        raise RuntimeError(_missing_dependencies_msg) from None
    obj = module if attribute is None else getattr(module, attribute)
    # Cache the result for future accesses
    globals()[name] = obj
    return obj


if t.TYPE_CHECKING:  # pragma: no cover
    import cv2
    import numpy as np

    from gaia_validators.image import SerializableImage, SerializableImagePayload
