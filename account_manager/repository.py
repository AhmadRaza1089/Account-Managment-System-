"""Persistence layer — translates domain objects to/from the database."""

import logging

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
