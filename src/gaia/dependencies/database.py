import typing as t

_uninstalled_dependencies = False

try:
    import sqlalchemy
except ImportError:  # pragma: no cover
    sqlalchemy = None
    _uninstalled_dependencies = True

try:
    import sqlalchemy_wrapper
except ImportError:  # pragma: no cover
    sqlalchemy_wrapper = None
    _uninstalled_dependencies = True


if t.TYPE_CHECKING:  # pragma: no cover
    import sqlalchemy

    import sqlalchemy_wrapper


def check_dependencies() -> None:
    if _uninstalled_dependencies is True:  # pragma: no cover
        raise RuntimeError(
            "All the dependencies required to use the database have not been "
            "installed. Run 'pip install . [database]' in your virtual "
            "environment to install them."
        )
