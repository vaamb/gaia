import typing as t

_uninstalled_dependencies = False

# AMQP and Redis are taken care of by dispatcher

try:
    import dispatcher
except ImportError:  # pragma: no cover
    dispatcher = None
    _uninstalled_dependencies = True


if t.TYPE_CHECKING:  # pragma: no cover
    import dispatcher


def check_dependencies() -> None:
    if _uninstalled_dependencies is True:
        raise RuntimeError(  # pragma: no cover
            "All the dependencies required to use the dispatcher have not been "
            "installed. Run 'pip install . [dispatcher]' in your virtual "
            "environment to install them."
        )
