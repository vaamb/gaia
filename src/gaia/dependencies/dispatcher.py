import typing as t

_uninstalled_dependencies = False

try:
    import kombu
except ImportError:
    kombu = None
    _uninstalled_dependencies = True

try:
    import dispatcher
except ImportError:
    dispatcher = None
    _uninstalled_dependencies = True


if t.TYPE_CHECKING:
    import kombu

    import dispatcher


def check_dependencies() -> None:
    if _uninstalled_dependencies is True:
        raise RuntimeError(
            "All the dependencies required to use the dispatcher have not been "
            "installed. Run 'pip install . [dispatcher]' in your virtual "
            "environment to install them."
        )
