"""Business operations.

Every function here takes an open Session and does its work inside the
caller's transaction, so a caller can compose several operations and have
them commit or roll back together.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .errors import (
    InsufficientFunds,
    InvalidAmount,
    InvalidState,
    NotFound,
    PermissionDenied,
)
from .models import (
    Actor,
    Balances,
    Company,
    Transaction,
    TransactionStatus,
    TransactionType,
    utcnow,
)

logger = logging.getLogger(__name__)

MAX_AMOUNT = Decimal("999999999999.99")  # fits Numeric(14, 2)
_CENTS = Decimal("0.01")


def parse_amount(value: str | int | float | Decimal) -> Decimal:
    """Turn user input into a positive money amount, or explain why not."""
    if isinstance(value, float):
        # Route through str so 0.1 + 0.2 style float error never enters the ledger.
        value = str(value)
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidAmount(f"{value!r} is not a valid amount.") from exc

    if not amount.is_finite():
        raise InvalidAmount("Amount must be a finite number.")
    if amount <= 0:
        raise InvalidAmount("Amount must be greater than zero.")
    if amount > MAX_AMOUNT:
        raise InvalidAmount(f"Amount must not exceed {MAX_AMOUNT}.")
    if amount.as_tuple().exponent < -2:  # type: ignore[operator]
        raise InvalidAmount("Amount must have at most 2 decimal places.")
    return amount.quantize(_CENTS)


# --------------------------------------------------------------------------
# Companies
# --------------------------------------------------------------------------


def create_company(session: Session, name: str, owner_name: str) -> Company:
    name = (name or "").strip()
    owner_name = (owner_name or "").strip()
    if not name:
        raise InvalidState("Company name must not be empty.")
    if not owner_name:
        raise InvalidState("Owner name must not be empty.")

    company = Company(name=name, owner_name=owner_name)
    session.add(company)
    session.flush()  # assigns company.id within this transaction
    logger.info("Created company %s (%s)", company.id, company.name)
    return company


def get_company(session: Session, company_id: int, *, lock: bool = False) -> Company:
    """Load a company, optionally locking the row for the rest of the transaction.

    Locking serialises concurrent spend decisions against the same company,
    so two expenses can't each be approved against the same funds.
    """
    stmt = select(Company).where(Company.id == company_id)
    if lock:
        # No-op on SQLite (which serialises writers anyway); real row lock
        # on MySQL and PostgreSQL.
        stmt = stmt.with_for_update()
    company = session.execute(stmt).scalar_one_or_none()
    if company is None:
        raise NotFound(f"No company with id {company_id}.")
    return company


def list_companies(session: Session) -> Sequence[Company]:
    return session.execute(select(Company).order_by(Company.id)).scalars().all()


# --------------------------------------------------------------------------
# Balances
# --------------------------------------------------------------------------


def get_balances(session: Session, company_id: int) -> Balances:
    """Sum the ledger. Never reads a stored total, so it cannot drift."""
    rows = session.execute(
        select(
            Transaction.type,
            Transaction.status,
            func.coalesce(func.sum(Transaction.amount), 0),
        )
        .where(Transaction.company_id == company_id)
        .group_by(Transaction.type, Transaction.status)
    ).all()

    income = expense = pending = Decimal("0")
    for txn_type, status, total in rows:
        total = Decimal(total or 0)
        if txn_type == TransactionType.INCOME and status == TransactionStatus.APPROVED:
            income += total
        elif txn_type == TransactionType.EXPENSE:
            if status == TransactionStatus.APPROVED:
                expense += total
            elif status == TransactionStatus.PENDING:
                pending += total
    return Balances(income=income, expense=expense, pending_expense=pending)


# --------------------------------------------------------------------------
# Money in
# --------------------------------------------------------------------------


def add_income(
    session: Session,
    company_id: int,
    actor: Actor,
    amount: str | int | float | Decimal,
    *,
    description: str | None = None,
    category: str | None = None,
) -> Transaction:
    """Record income. Admins only."""
    if not actor.is_admin:
        raise PermissionDenied(
            f"{actor.name} has role '{actor.role.value}' and cannot record income."
        )
    value = parse_amount(amount)
    get_company(session, company_id)  # existence check

    txn = Transaction(
        company_id=company_id,
        type=TransactionType.INCOME,
        status=TransactionStatus.APPROVED,
        amount=value,
        category=category,
        description=description,
        created_by=actor.name,
        decided_by=actor.name,
        decided_at=utcnow(),
    )
    session.add(txn)
    session.flush()
    logger.info("Company %s: income %s recorded by %s", company_id, value, actor.name)
    return txn


# --------------------------------------------------------------------------
# Money out
# --------------------------------------------------------------------------


def submit_expense(
    session: Session,
    company_id: int,
    actor: Actor,
    amount: str | int | float | Decimal,
    *,
    description: str | None = None,
    category: str | None = None,
) -> Transaction:
    """Spend, or request to spend.

    An admin's expense is approved immediately; anyone else's waits for
    approval. Either way it must fit within the available balance, so the
    books can never be committed past what the company actually holds.
    """
    value = parse_amount(amount)
    get_company(session, company_id, lock=True)

    balances = get_balances(session, company_id)
    if value > balances.available:
        raise InsufficientFunds(
            f"Expense of {value} exceeds the available balance of "
            f"{balances.available} (balance {balances.balance}, "
            f"{balances.pending_expense} already awaiting approval)."
        )

    status = (
        TransactionStatus.APPROVED if actor.is_admin else TransactionStatus.PENDING
    )
    txn = Transaction(
        company_id=company_id,
        type=TransactionType.EXPENSE,
        status=status,
        amount=value,
        category=category,
        description=description,
        created_by=actor.name,
        decided_by=actor.name if actor.is_admin else None,
        decided_at=utcnow() if actor.is_admin else None,
    )
    session.add(txn)
    session.flush()
    logger.info(
        "Company %s: expense %s by %s -> %s",
        company_id,
        value,
        actor.name,
        status.value,
    )
    return txn


def _load_pending_expense(session: Session, transaction_id: int) -> Transaction:
    txn = session.get(Transaction, transaction_id)
    if txn is None:
        raise NotFound(f"No transaction with id {transaction_id}.")
    if txn.type is not TransactionType.EXPENSE:
        raise InvalidState(f"Transaction {transaction_id} is not an expense.")
    if txn.status is not TransactionStatus.PENDING:
        raise InvalidState(
            f"Transaction {transaction_id} is already {txn.status.value}."
        )
    return txn


def approve_expense(
    session: Session, transaction_id: int, actor: Actor, *, note: str | None = None
) -> Transaction:
    """Approve a pending expense. Admins only."""
    if not actor.is_admin:
        raise PermissionDenied(
            f"{actor.name} has role '{actor.role.value}' and cannot approve expenses."
        )
    txn = _load_pending_expense(session, transaction_id)
    get_company(session, txn.company_id, lock=True)

    txn.status = TransactionStatus.APPROVED
    txn.decided_by = actor.name
    txn.decided_at = utcnow()
    txn.decision_note = note
    session.flush()
    logger.info("Transaction %s approved by %s", transaction_id, actor.name)
    return txn


def reject_expense(
    session: Session, transaction_id: int, actor: Actor, *, note: str | None = None
) -> Transaction:
    """Reject a pending expense, releasing the funds it was holding."""
    if not actor.is_admin:
        raise PermissionDenied(
            f"{actor.name} has role '{actor.role.value}' and cannot reject expenses."
        )
    txn = _load_pending_expense(session, transaction_id)

    txn.status = TransactionStatus.REJECTED
    txn.decided_by = actor.name
    txn.decided_at = utcnow()
    txn.decision_note = note
    session.flush()
    logger.info("Transaction %s rejected by %s", transaction_id, actor.name)
    return txn


# --------------------------------------------------------------------------
# Reading the ledger
# --------------------------------------------------------------------------


def list_transactions(
    session: Session,
    company_id: int,
    *,
    status: TransactionStatus | None = None,
    type: TransactionType | None = None,
    limit: int = 100,
) -> Sequence[Transaction]:
    stmt = select(Transaction).where(Transaction.company_id == company_id)
    if status is not None:
        stmt = stmt.where(Transaction.status == status)
    if type is not None:
        stmt = stmt.where(Transaction.type == type)
    stmt = stmt.order_by(Transaction.created_at.desc(), Transaction.id.desc())
    stmt = stmt.limit(max(1, min(limit, 1000)))
    return session.execute(stmt).scalars().all()


def get_report(session: Session, company_id: int) -> dict:
    company = get_company(session, company_id)
    balances = get_balances(session, company_id)
    pending = list_transactions(
        session, company_id, status=TransactionStatus.PENDING, limit=50
    )
    return {
        "company_id": company.id,
        "company": company.name,
        "owner": company.owner_name,
        **balances.as_dict(),
        "pending_count": len(pending),
        "pending_transactions": [t.as_dict() for t in pending],
    }
