import os

import pytest

from account_manager import db
from account_manager.config import Settings
from account_manager.models import Actor, Role

# CI sets this to run the whole suite against a real MySQL and PostgreSQL
# server as well as SQLite. Locally it's unset and everything uses a
# throwaway SQLite file, so there's nothing to install to run the tests.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

# Row locking (SELECT ... FOR UPDATE) is a no-op on SQLite, so tests that
# genuinely need it only mean something on a real server.
requires_real_database = pytest.mark.skipif(
    not TEST_DATABASE_URL or TEST_DATABASE_URL.startswith("sqlite"),
    reason="needs a database with real row locking (set TEST_DATABASE_URL)",
)


@pytest.fixture
def database(tmp_path, monkeypatch):
    """A clean database for one test."""
    if TEST_DATABASE_URL:
        url = TEST_DATABASE_URL
        monkeypatch.setenv("DATABASE_URL", url)
        db.init_engine(Settings(database_url=url))
        db.drop_all()  # the server is shared between tests
    else:
        url = f"sqlite:///{tmp_path / 'test.db'}"
        monkeypatch.setenv("DATABASE_URL", url)
        db.init_engine(Settings(database_url=url))
    db.create_all()
    return url


@pytest.fixture
def session(database):
    with db.session_scope() as open_session:
        yield open_session


@pytest.fixture
def admin():
    return Actor(name="Ahmed", role=Role.ADMIN)


@pytest.fixture
def staff():
    return Actor(name="Raza", role=Role.REGULAR_USER)
