try:
    import sqlalchemy
except ImportError:
    raise RuntimeError(
        "sqlalchemy is required to log data to a database. Run "
        "`pip install sqlalchemy` in your virtual env"
    )

from . import models
from . import routines
from .wrapper import SQLAlchemyWrapper
