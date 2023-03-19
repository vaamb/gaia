try:
    import sqlalchemy
except ImportError:
    raise RuntimeError(
        "sqlalchemy is required to log data to a database. Run "
        "`pip install sqlalchemy` in your virtual env"
    )

from gaia.database import models, routines
from gaia.database.models import db
