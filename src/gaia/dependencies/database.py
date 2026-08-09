from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec
import typing as t


_lazy_objects: dict[str, tuple[str, str | None]] = {
    "sqlalchemy": ("sqlalchemy", None),
    "sqlalchemy_wrapper": ("sqlalchemy_wrapper", None),
}

_missing_dependencies_msg = (
    "All the dependencies required to use the database have not been "
    "installed. Run 'uv sync --inexact --extra database' in your virtual "
    "environment to install them."
)


def _is_available(module_name: str) -> bool:
    try:
        return find_spec(module_name) is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


def check_dependencies() -> None:
    for module_name in ("sqlalchemy", "sqlalchemy_wrapper"):
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
    import sqlalchemy

    import sqlalchemy_wrapper
