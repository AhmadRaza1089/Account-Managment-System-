"""Migration tests.

The upgrade path matters more than usual here: this project is meant to be
self-hosted, so we can't log into anyone's database to fix it by hand. A
release that loses their data is unrecoverable for them.
"""

from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from account_manager import db, services
from account_manager.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent

LEGACY_SCHEMA = (
    """
    CREATE TABLE Company (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name VARCHAR(50) NOT NULL,
        owner_name VARCHAR(50) NOT NULL
    )
    """,
    """
    CREATE TABLE Account (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INT NOT NULL UNIQUE,
        income DECIMAL(12, 2) NOT NULL DEFAULT 0,
        expense DECIMAL(12, 2) NOT NULL DEFAULT 0,
        pending_expense DECIMAL(12, 2) NOT NULL DEFAULT 0,
        FOREIGN KEY (company_id) REFERENCES Company(id)
    )
    """,
)


def _alembic_config() -> Config:
    """The same configuration the application itself uses."""
    from account_manager.migrate import alembic_config

    return alembic_config()


@pytest.fixture
def migrated_url(tmp_path, monkeypatch):
    """SQLite only — the legacy-schema tests below use SQLite-specific DDL."""
    url = f"sqlite:///{tmp_path / 'migrate.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    return url


@pytest.fixture
def any_database_url(tmp_path, monkeypatch):
    """The configured test database, so migration DDL is checked for
    portability across MySQL and PostgreSQL as well as SQLite."""
    from tests.conftest import TEST_DATABASE_URL

    if TEST_DATABASE_URL:
        url = TEST_DATABASE_URL
        monkeypatch.setenv("DATABASE_URL", url)
        db.init_engine(Settings(database_url=url))
        db.drop_all()
    else:
        url = f"sqlite:///{tmp_path / 'migrate.db'}"
        monkeypatch.setenv("DATABASE_URL", url)
    return url


def test_migration_creates_a_working_schema_from_scratch(any_database_url):
    command.upgrade(_alembic_config(), "head")

    tables = set(sa.inspect(sa.create_engine(any_database_url)).get_table_names())
    assert {"companies", "transactions"} <= tables

    db.init_engine(Settings(database_url=any_database_url))
    with db.session_scope() as session:
        company = services.create_company(session, "Acme", "Ahmed")
        assert company.id is not None


def test_upgrading_an_old_install_preserves_the_money(migrated_url):
    """The previous release stored running totals in Company/Account.
    Those totals must survive as ledger entries."""
    engine = sa.create_engine(migrated_url)
    with engine.begin() as conn:
        for statement in LEGACY_SCHEMA:
            conn.execute(sa.text(statement))
        conn.execute(
            sa.text(
                "INSERT INTO Company (id, name, owner_name) "
                "VALUES (1, 'OldCorp', 'Ahmed'), (2, 'Empty Ltd', 'Sara')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO Account (company_id, income, expense, pending_expense) "
                "VALUES (1, 10000.00, 2500.00, 750.00), (2, 0, 0, 0)"
            )
        )
    engine.dispose()

    command.upgrade(_alembic_config(), "head")

    db.init_engine(Settings(database_url=migrated_url))
    with db.session_scope() as session:
        companies = services.list_companies(session)
        assert [c.name for c in companies] == ["OldCorp", "Empty Ltd"]

        balances = services.get_balances(session, 1)
        assert balances.income == Decimal("10000.00")
        assert balances.expense == Decimal("2500.00")
        assert balances.pending_expense == Decimal("750.00")
        assert balances.balance == Decimal("7500.00")

        # The company with nothing in it still comes across.
        assert services.get_balances(session, 2).balance == Decimal("0")

    # The old tables are gone once their data has been carried over.
    tables = set(sa.inspect(sa.create_engine(migrated_url)).get_table_names())
    assert "Account" not in tables
    assert "Company" not in tables


def test_migrated_pending_expense_can_finally_be_approved(migrated_url):
    """Pending amounts were previously a dead end with no approval path.
    After migrating they become real transactions that can be decided."""
    engine = sa.create_engine(migrated_url)
    with engine.begin() as conn:
        for statement in LEGACY_SCHEMA:
            conn.execute(sa.text(statement))
        conn.execute(
            sa.text("INSERT INTO Company (id, name, owner_name) VALUES (1, 'Old', 'A')")
        )
        conn.execute(
            sa.text(
                "INSERT INTO Account (company_id, income, expense, pending_expense) "
                "VALUES (1, 1000.00, 0, 400.00)"
            )
        )
    engine.dispose()

    command.upgrade(_alembic_config(), "head")

    from account_manager.models import Actor, Role, TransactionStatus

    db.init_engine(Settings(database_url=migrated_url))
    with db.session_scope() as session:
        pending = services.list_transactions(
            session, 1, status=TransactionStatus.PENDING
        )
        assert len(pending) == 1
        services.approve_expense(session, pending[0].id, Actor("A", Role.ADMIN))
        assert services.get_balances(session, 1).expense == Decimal("400.00")


# ------------------------------------------------- init-db / apply_migrations


def test_init_db_records_the_migration_version(any_database_url):
    """Regression: init-db used to create tables straight from the models
    with no version record, so the documented upgrade step then tried to
    create those same tables again and failed."""
    from account_manager.migrate import apply_migrations, current_revision

    db.init_engine(Settings(database_url=any_database_url))
    apply_migrations()

    assert current_revision() is not None
    # Running it a second time is a no-op, not an error.
    apply_migrations()


def test_init_db_recovers_a_database_that_has_no_version_record(any_database_url):
    """A schema built by an older create_all() has the tables but no version.
    Stamping it first means the migrations don't try to recreate them."""
    from account_manager.migrate import apply_migrations, current_revision

    db.init_engine(Settings(database_url=any_database_url))
    command.upgrade(_alembic_config(), "0001_initial_ledger")
    with sa.create_engine(any_database_url).begin() as conn:
        conn.execute(sa.text("DROP TABLE alembic_version"))

    apply_migrations()  # must not fail with "table already exists"

    assert current_revision() == "0003_authentication"
    with db.session_scope() as session:
        assert services.create_company(session, "Acme", "Ahmed").id is not None


def test_the_migrations_ship_inside_the_package(any_database_url):
    """They must be importable from an installed wheel, since a pip user has
    no source checkout and no alembic.ini to run the alembic command with."""
    from account_manager import migrate

    assert migrate.MIGRATIONS_DIR.is_dir()
    assert (migrate.MIGRATIONS_DIR / "env.py").is_file()
    assert list((migrate.MIGRATIONS_DIR / "versions").glob("*.py"))
    assert migrate.MIGRATIONS_DIR.parent.name == "account_manager"
