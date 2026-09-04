"""Persistence layer — translates domain objects to/from the database."""

import logging
from decimal import Decimal

from . import db
from .models import Account, Company

logger = logging.getLogger(__name__)


def insert_company(company: Company) -> int:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO Company (name, owner_name) VALUES (%s, %s)",
            (company.name, company.owner_name),
        )
        conn.commit()
        company_id = cursor.lastrowid
        cursor.close()
    if company_id is None:
        raise RuntimeError("Insert into Company did not return a row id.")
    company.id = company_id
    return company_id


def insert_account(account: Account) -> int:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO Account (company_id, income, expense, pending_expense)
            VALUES (%s, %s, %s, %s)
            """,
            (account.company_id, account.income, account.expense, account.pending_expense),
        )
        conn.commit()
        account_id = cursor.lastrowid
        cursor.close()
    if account_id is None:
        raise RuntimeError("Insert into Account did not return a row id.")
    account.id = account_id
    return account_id


def save_account(account: Account) -> None:
    if account.id is None:
        raise ValueError("Cannot save an Account that hasn't been inserted yet.")
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE Account
            SET income = %s, expense = %s, pending_expense = %s
            WHERE id = %s
            """,
            (account.income, account.expense, account.pending_expense, account.id),
        )
        conn.commit()
        cursor.close()
    logger.info(
        "Account %s persisted (income=%s expense=%s pending=%s)",
        account.id,
        account.income,
        account.expense,
        account.pending_expense,
    )


def get_company(company_id: int) -> Company | None:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, owner_name FROM Company WHERE id = %s", (company_id,)
        )
        row = cursor.fetchone()
        cursor.close()
    if row is None:
        return None
    return Company(id=row[0], name=row[1], owner_name=row[2])


def list_companies() -> list[Company]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, owner_name FROM Company ORDER BY id")
        rows = cursor.fetchall()
        cursor.close()
    return [Company(id=row[0], name=row[1], owner_name=row[2]) for row in rows]


def get_account_by_company(company_id: int) -> Account | None:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, company_id, income, expense, pending_expense
            FROM Account WHERE company_id = %s
            """,
            (company_id,),
        )
        row = cursor.fetchone()
        cursor.close()
    if row is None:
        return None
    return Account(
        id=row[0],
        company_id=row[1],
        income=Decimal(row[2]),
        expense=Decimal(row[3]),
        pending_expense=Decimal(row[4]),
    )
