from contextlib import contextmanager

from sqlalchemy.engine import create_engine, Engine
from sqlalchemy.orm import scoped_session, sessionmaker

from .models import base
from src.utils import base_dir


class SQLAlchemyWrapper:
    """Wrapper to use SQLAlchemy in parallel of Flask-SQLAlchemy
    outside of app context

    For a safe use, use as follow:
    ``
    db = SQLAlchemyWrapper()
    with db.scoped_session() as session:
        session.your_query_here
    ``
    This will automatically create a scoped session and remove it at the end of
    the scope.
    """
    _Model = base

    def __init__(self, config=None, model=_Model, create=False):
        self.Model = model

        self._initialized = False
        self._session_factory = sessionmaker()
        self._session = scoped_session(None)  # For type hint only
        self._engines = {}
        self._config = None

        if config:
            self.init(config)

        if create:
            if not config:
                raise RuntimeError(
                    "Cannot create tables if no config is provided"
                )
            else:
                self.create_all()

    @property
    def session(self):
        if not self._initialized:
            raise RuntimeError(
                "No config option was provided. Use db.init(config) to finish "
                "db initialization"
            )
        else:
            return self._session()

    def init(self, config_class) -> None:
        try:
            uri = getattr(config_class, "DATABASE_URI")
        except AttributeError:
            db_file = base_dir/"gaia_data.db"
            uri = f"sqlite:///{db_file}"
        self._config = {"DATABASE_URI": uri}
        from . import models
        self._session_factory = sessionmaker(binds=self.get_binds_mapping())
        self._session = scoped_session(self._session_factory)
        self._initialized = True

    def _get_tables_for_bind(self, bind: str = None) -> list:
        return [
            table for table in self.Model.metadata.tables.values()
            if table.info.get("bind_key", None) == bind
        ]

    def _get_uri_for_bind(self, bind: str = None) -> str:
        if bind is None:
            return self._config["DATABASE_URI"]
        binds = self._config.get("DATABASE_BINDS", ())
        assert bind in binds, f"Set bind {bind} in the config "\
                              f"'DATABASE_BINDS' in order to use it."
        return binds[bind]

    def _get_engine_for_bind(self, bind: str = None) -> Engine:
        assert self._config, "SQLAlchemyWrapper was not fully initialized"
        engine = self._engines.get(bind, None)
        if engine is None:
            engine = create_engine(self._get_uri_for_bind(bind), convert_unicode=True)
            self._engines[bind] = engine
        return engine

    @contextmanager
    def scoped_session(self):
        try:
            yield self._session()
        except Exception as e:
            self._session.rollback()
            raise e
        finally:
            self._session.remove()

    def get_binds_mapping(self) -> dict:
        binds = [None] + list(self._config.get("DATABASE_BINDS", ()))
        result = {}
        for bind in binds:
            engine = self._get_engine_for_bind(bind)
            result.update(
                {table: engine for table in self._get_tables_for_bind(bind)})
        return result

    def create_all(self):
        binds = [None] + list(self._config.get("DATABASE_BINDS", ()))
        for bind in binds:
            engine = self._get_engine_for_bind(bind)
            tables = self._get_tables_for_bind(bind)
            self.Model.metadata.create_all(bind=engine, tables=tables)

    def drop_all(self):
        binds = [None] + list(self._config.get("DATABASE_BINDS", ()))
        for bind in binds:
            engine = self._get_engine_for_bind(bind)
            tables = self._get_tables_for_bind(bind)
            self.Model.metadata.drop_all(bind=engine, tables=tables)

    def close(self):
        return self._session.remove()
