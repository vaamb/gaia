from __future__ import annotations

from importlib import import_module
import typing as t


ModuleGroup = t.Literal["camera", "database", "dispatcher"]


_lazy_objects: dict[str, tuple[str, str | None, ModuleGroup]] = {
    # camera
    "np": ("numpy", None, "camera"),
    "cv2": ("cv2", None, "camera"),
    "SerializableImage": ("gaia_validators.image", "SerializableImage", "camera"),
    "SerializableImagePayload": ("gaia_validators.image", "SerializableImagePayload", "camera"),
    # database
    "sqlalchemy": ("sqlalchemy", None, "database"),
    "sqlalchemy_wrapper": ("sqlalchemy_wrapper", None, "database"),
    # dispatcher
    "AsyncAMQPDispatcher": ("dispatcher", "AsyncAMQPDispatcher", "dispatcher"),
    "AsyncInMemoryDispatcher": ("dispatcher", "AsyncInMemoryDispatcher", "dispatcher"),
    "AsyncRedisDispatcher": ("dispatcher", "AsyncRedisDispatcher", "dispatcher"),
}


def _get_missing_dependencies_msg(module_group: ModuleGroup) -> str:
    return (
        f"All the dependencies required to use the {module_group} have not been "
        f"installed. Run 'uv sync --inexact --extra {module_group}' in your virtual "
        f"environment to install them."
    )


_availability: dict[str, bool] = {}


def _is_available(module_name: str) -> bool:
    if module_name in _availability:
        return _availability[module_name]
    try:
        import_module(module_name)
    except Exception:  # pragma: no cover
        available = False
    else:
        available = True
    _availability[module_name] = available
    return available


def check_dependencies(module_group: ModuleGroup) -> None:
    if module_group == "camera":
        for module_name in ("numpy", "cv2", "gaia_validators.image"):
            if not _is_available(module_name):  # pragma: no cover
                raise RuntimeError(_get_missing_dependencies_msg("camera"))

    elif module_group == "database":
        for module_name in ("sqlalchemy", "sqlalchemy_wrapper"):
            if not _is_available(module_name):  # pragma: no cover
                raise RuntimeError(_get_missing_dependencies_msg("database"))

    elif module_group == "dispatcher":
        for module_name in ("dispatcher",):
            if not _is_available(module_name):  # pragma: no cover
                raise RuntimeError(_get_missing_dependencies_msg("dispatcher"))

    else:
        raise ValueError(f"module {module_group!r} is not available")


def __getattr__(name: str) -> t.Any:
    try:
        module_name, attribute, module_group = _lazy_objects[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    try:
        module = import_module(module_name)
        obj = module if attribute is None else getattr(module, attribute)
    except ImportError:  # pragma: no cover
        raise RuntimeError(_get_missing_dependencies_msg(module_group))
    # Cache the result for future accesses
    globals()[name] = obj
    return obj


if t.TYPE_CHECKING:  # pragma: no cover
    import cv2
    import numpy as np
    import sqlalchemy

    from dispatcher import (
        AsyncAMQPDispatcher, AsyncInMemoryDispatcher, AsyncRedisDispatcher)
    from gaia_validators.image import SerializableImage, SerializableImagePayload
    import sqlalchemy_wrapper
