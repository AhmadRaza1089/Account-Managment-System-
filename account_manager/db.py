"""Engine and session management.

Everything that writes goes through session_scope(), which gives you one
transaction that commits on success and rolls back on any error. Multi-step
operations therefore either fully happen or don't happen at all.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import Base

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _engine_kwargs(url: str, echo: bool) -> dict:
    kwargs: dict = {"echo": echo, "future": True}
    if url.startswith("sqlite"):
        # Allow use from multiple threads (the MCP server is async).
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # Recycle connections so a server that sits idle overnight doesn't
        # hand out sockets the database has already dropped.
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 3600
    return kwargs


def init_engine(settings: Settings | None = None) -> Engine:
    """Create the engine and session factory. Call once at startup."""
    global _engine, _SessionFactory
    settings = settings or Settings.from_env()
    engine = create_engine(
        settings.database_url, **_engine_kwargs(settings.database_url, settings.echo_sql)
    )

    if settings.database_url.startswith("sqlite"):
        # SQLite ignores foreign keys unless asked, which would let us
        # orphan transactions.
        @event.listens_for(engine, "connect")
        def _enable_sqlite_fk(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    _engine = engine
    _SessionFactory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine


def get_engine() -> Engine:
    if _engine is None:
        return init_engine()
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """One unit of work: commits on success, rolls back on any exception."""
    if _SessionFactory is None:
        init_engine()
    assert _SessionFactory is not None
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        logger.debug("Transaction rolled back.", exc_info=True)
        raise
    finally:
        session.close()


def create_all() -> None:
    """Create tables directly from the models.

    Convenient for tests and first runs. Installations that need to survive
    upgrades should use the Alembic migrations instead (`alembic upgrade head`).
    """
    Base.metadata.create_all(get_engine())
