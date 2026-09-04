import pytest

from account_manager import db
from account_manager.config import Settings
from account_manager.models import Actor, Role


@pytest.fixture
def database(tmp_path, monkeypatch):
    """A fresh SQLite database per test."""
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
