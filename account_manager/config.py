"""Configuration.

The only setting that matters is the database URL. It defaults to a local
SQLite file so the project runs with zero setup; point DATABASE_URL at
MySQL or PostgreSQL when you want a real server.
"""

import os
from dataclasses import dataclass
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()

DEFAULT_DATABASE_URL = "sqlite:///account_manager.db"

# Older versions of this project configured MySQL with these separate
# variables. They still work, so existing installs keep running after an
# upgrade, but DATABASE_URL takes precedence.
_LEGACY_VARS = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")


def _legacy_mysql_url() -> str | None:
    """Build a MySQL URL from the pre-DATABASE_URL environment variables."""
    if not all(os.environ.get(name) for name in _LEGACY_VARS):
        return None
    user = quote_plus(os.environ["DB_USER"])
    password = quote_plus(os.environ["DB_PASSWORD"])
    host = os.environ["DB_HOST"]
    port = os.environ.get("DB_PORT", "3306")
    name = os.environ["DB_NAME"]
    return f"mysql+mysqlconnector://{user}:{password}@{host}:{port}/{name}"


@dataclass(frozen=True)
class Settings:
    database_url: str
    echo_sql: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        url = os.environ.get("DATABASE_URL") or _legacy_mysql_url() or DEFAULT_DATABASE_URL
        return cls(
            database_url=url,
            echo_sql=os.environ.get("ECHO_SQL", "").lower() in {"1", "true", "yes"},
        )
