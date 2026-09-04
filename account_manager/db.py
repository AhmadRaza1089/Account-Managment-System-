import logging
from collections.abc import Iterator
from contextlib import contextmanager

import mysql.connector
from mysql.connector.pooling import MySQLConnectionPool

from .config import DatabaseSettings

logger = logging.getLogger(__name__)

_pool: MySQLConnectionPool | None = None


def init_pool(settings: DatabaseSettings, pool_size: int = 5) -> MySQLConnectionPool:
    """Create the module-level connection pool. Call once at startup."""
    global _pool
    _pool = MySQLConnectionPool(
        pool_name="account_manager_pool",
        pool_size=pool_size,
        host=settings.host,
        port=settings.port,
        user=settings.user,
        password=settings.password,
        database=settings.database,
    )
    return _pool


@contextmanager
def get_connection() -> Iterator["mysql.connector.pooling.PooledMySQLConnection"]:
    """Borrow a connection from the pool; rolls back and re-raises on error."""
    if _pool is None:
        raise RuntimeError("Connection pool not initialized; call init_pool() first.")
    conn = _pool.get_connection()
    try:
        yield conn
    except Exception:
        conn.rollback()
        logger.exception("Database operation failed; transaction rolled back.")
        raise
    finally:
        conn.close()  # returns the connection to the pool, doesn't actually close it


def init_schema() -> None:
    """Create tables if they don't already exist."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS Company (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(50) NOT NULL,
                owner_name VARCHAR(50) NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS Account (
                id INT AUTO_INCREMENT PRIMARY KEY,
                company_id INT NOT NULL UNIQUE,
                income DECIMAL(12, 2) NOT NULL DEFAULT 0,
                expense DECIMAL(12, 2) NOT NULL DEFAULT 0,
                pending_expense DECIMAL(12, 2) NOT NULL DEFAULT 0,
                FOREIGN KEY (company_id) REFERENCES Company(id)
            )
            """
        )
        conn.commit()
        cursor.close()
