import os

import pytest

from account_manager import auth, db
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
    """A clean database for one test.

    Also points the credentials file at a temporary directory, so a test
    can never read or overwrite the real one in the developer's home.
    """
    monkeypatch.setenv("ACCOUNT_MANAGER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv(auth.TOKEN_ENV_VAR, raising=False)

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
def superuser(database):
    """A logged-in superuser, with credentials saved as the CLI would."""
    with db.session_scope() as setup:
        user = auth.create_user(setup, "ahmed", "correct-horse", is_superuser=True)
        token = auth.issue_token(setup, user, name="test")
        username = user.username
    auth.save_credentials(username, token)
    return {"username": username, "token": token}


@pytest.fixture
def staff_user(database):
    """A second, ordinary account — no companies until granted."""
    with db.session_scope() as setup:
        user = auth.create_user(setup, "raza", "correct-horse")
        token = auth.issue_token(setup, user, name="test")
        username = user.username
    return {"username": username, "token": token}


@pytest.fixture
def login_as(monkeypatch):
    """Switch which account subsequent commands run as."""

    def _login(credential):
        monkeypatch.setenv(auth.TOKEN_ENV_VAR, credential["token"])

    return _login


@pytest.fixture
def mcp_credential(superuser, monkeypatch):
    """Authenticate the MCP server as the superuser."""
    monkeypatch.setenv(auth.TOKEN_ENV_VAR, superuser["token"])
    return superuser


@pytest.fixture
def admin():
    """An Actor for testing the service layer directly."""
    return Actor(name="Ahmed", role=Role.ADMIN)


@pytest.fixture
def staff():
    return Actor(name="Raza", role=Role.REGULAR_USER)
