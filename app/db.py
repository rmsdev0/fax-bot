from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

# The migration the very first production schema corresponds to; databases
# created by create_all before Alembic existed get stamped with this.
BASELINE_REVISION = "0001"
MIGRATION_LOCK_KEY = 0x00FAB07  # arbitrary constant for pg_advisory_lock


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine():
    url = get_settings().database_url
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        # Server and worker are separate processes sharing the file.
        kwargs["connect_args"] = {"timeout": 30}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _set_wal(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")

    return engine


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def init_db() -> None:
    from app import models  # noqa: F401  (register tables)

    if get_settings().database_url.startswith("sqlite"):
        # Tests and throwaway dev DBs: schema straight from the models.
        Base.metadata.create_all(get_engine())
        return
    _run_migrations()


def _run_migrations() -> None:
    """Bring a Postgres database to the current schema via Alembic.

    Both the web and worker processes call this at startup; the advisory lock
    serializes them. A database created by the pre-Alembic create_all path is
    recognized (tables but no alembic_version) and stamped as the baseline.
    """
    from alembic.config import Config

    from alembic import command

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))

    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        try:
            inspector = inspect(engine)
            if inspector.has_table("threads") and not inspector.has_table("alembic_version"):
                command.stamp(cfg, BASELINE_REVISION)
            command.upgrade(cfg, "head")
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
