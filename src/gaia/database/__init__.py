from gaia.dependencies import check_dependencies

check_dependencies("database")

from gaia.database import models, routines
from gaia.database.models import db
