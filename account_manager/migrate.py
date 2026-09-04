"""Applying database migrations.

The migrations ship inside the package, so this works for someone who
installed with pip and has no source checkout — they have no alembic.ini
to run the `alembic` command against.

This is the only supported way to create or upgrade the schema.
`db.create_all()` still exists for tests, but using it on a real database
leaves no version record, and the next upgrade then tries to create tables
that are already there.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from .db import get_engine

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# Which revision a database is already at, for schemas that were created
# directly from the models before this ran migrations. Checked newest
# first; the first match wins.
_SCHEMA_MARKERS = (
    ("0003_authentication", lambda tables, columns: "users" in tables),
    ("0002_dates_currency_reversal", lambda tables, columns: "occurred_on" in columns),
    ("0001_initial_ledger", lambda tables, columns: "companies" in tables),
)


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def current_revision() -> str | None:
    with get_engine().connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _existing_schema_revision() -> str | None:
    """Work out which migration a versionless but populated database matches."""
    inspector = inspect(get_engine())
    tables = set(inspector.get_table_names())
    if not tables:
        return None

    columns: set[str] = set()
    if "transactions" in tables:
        columns = {column["name"] for column in inspector.get_columns("transactions")}

    for revision, matches in _SCHEMA_MARKERS:
        if matches(tables, columns):
            return revision
    return None


def apply_migrations() -> None:
    """Bring the database up to date, whatever state it starts in."""
    config = alembic_config()

    if current_revision() is None:
        # No version record. Either the database is empty (migrate normally),
        # or its tables were created directly from the models by an older
        # version of this project — in which case record where it already is
        # first, so the migrations don't try to create them again.
        already_at = _existing_schema_revision()
        if already_at is not None:
            logger.info("Existing schema detected; stamping %s", already_at)
            command.stamp(config, already_at)

    command.upgrade(config, "head")


def schema_is_ready() -> bool:
    """Whether the tables the application needs are present."""
    return "transactions" in set(inspect(get_engine()).get_table_names())
