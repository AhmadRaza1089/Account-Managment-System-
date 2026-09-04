import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

_REQUIRED_VARS = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    user: str
    password: str
    database: str
    port: int = 3306

    @classmethod
    def from_env(cls) -> "DatabaseSettings":
        missing = [name for name in _REQUIRED_VARS if not os.environ.get(name)]
        if missing:
            raise RuntimeError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                "Copy .env.example to .env and fill in your database credentials."
            )
        return cls(
            host=os.environ["DB_HOST"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            database=os.environ["DB_NAME"],
            port=int(os.environ.get("DB_PORT", "3306")),
        )
