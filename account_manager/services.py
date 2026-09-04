"""Business operations.

Every function here takes an open Session and does its work inside the
caller's transaction, so a caller can compose several operations and have
them commit or roll back together.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Sequence
from datetime import date
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
    DEFAULT_CURRENCY,
    Actor,
    Balances,
    Company,
    CompanyMember,
    Role,
    Transaction,
    TransactionStatus,
    TransactionType,
    User,
    utcnow,
    utctoday,
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


def create_company(
    session: Session,
    name: str,
    owner_name: str,
    *,
    currency: str = DEFAULT_CURRENCY,
    creator: User | None = None,
) -> Company:
    """Create a company.

    Whoever creates it becomes an admin of it, so they can immediately see
    and manage what they just made. Superusers already reach every company,
    so they need no explicit membership row.
    """
    name = (name or "").strip()
    owner_name = (owner_name or "").strip()
    if not name:
        raise InvalidState("Company name must not be empty.")
    if not owner_name:
        raise InvalidState("Owner name must not be empty.")

    currency = (currency or DEFAULT_CURRENCY).strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise InvalidState(
            f"Currency must be a three-letter code such as USD or PKR, not {currency!r}."
        )

    company = Company(name=name, owner_name=owner_name, currency=currency)
    session.add(company)
    session.flush()  # assigns company.id within this transaction

    if creator is not None and not creator.is_superuser:
        session.add(
            CompanyMember(company_id=company.id, user_id=creator.id, role=Role.ADMIN)
        )
        session.flush()

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


def get_balances(
    session: Session,
    company_id: int,
    *,
    since: date | None = None,
    until: date | None = None,
) -> Balances:
    """Sum the ledger. Never reads a stored total, so it cannot drift.

    With no dates this is the company's current position, which is what the
    spending checks use. With dates it is a summary of that period — useful
    for a monthly report, but not a spendable amount.
    """
    stmt = (
        select(
            Transaction.type,
            Transaction.status,
            func.coalesce(func.sum(Transaction.amount), 0),
        )
        .where(Transaction.company_id == company_id)
        .group_by(Transaction.type, Transaction.status)
    )
    if since is not None:
        stmt = stmt.where(Transaction.occurred_on >= since)
    if until is not None:
        stmt = stmt.where(Transaction.occurred_on <= until)

    rows = session.execute(stmt).all()

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
    occurred_on: date | None = None,
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
        occurred_on=occurred_on or utctoday(),
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
    occurred_on: date | None = None,
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
        occurred_on=occurred_on or utctoday(),
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


def company_id_for_transaction(session: Session, transaction_id: int) -> int:
    """Which company a transaction belongs to.

    Callers need this before they can work out what authority the user has,
    since permissions are granted per company.
    """
    return _company_id_for(session, transaction_id)


def _company_id_for(session: Session, transaction_id: int) -> int:
    company_id = session.execute(
        select(Transaction.company_id).where(Transaction.id == transaction_id)
    ).scalar_one_or_none()
    if company_id is None:
        raise NotFound(f"No transaction with id {transaction_id}.")
    return company_id


def _claim_pending_expense(session: Session, transaction_id: int) -> Transaction:
    """Re-read an expense under lock and check it is still pending.

    Both parts matter. Without the re-read, two admins approving at the same
    moment both see 'pending' and both apply the expense, spending the money
    twice; populate_existing forces the values to come from the database
    rather than from whatever this session already had in memory.

    The re-read must also be a *locking* read. MySQL defaults to REPEATABLE
    READ, where a plain SELECT returns the snapshot taken when the
    transaction began — so it would still report 'pending' even after the
    other approver committed. A locking read always sees the latest
    committed row, on MySQL and PostgreSQL alike.
    """
    txn = session.execute(
        select(Transaction)
        .where(Transaction.id == transaction_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()

    if txn is None:
        raise NotFound(f"No transaction with id {transaction_id}.")
    if txn.type is not TransactionType.EXPENSE:
        raise InvalidState(f"Transaction {transaction_id} is not an expense.")
    if txn.status is not TransactionStatus.PENDING:
        raise InvalidState(
            f"Transaction {transaction_id} is already {txn.status.value}."
        )
    return txn


def _decide_expense(
    session: Session,
    transaction_id: int,
    actor: Actor,
    status: TransactionStatus,
    note: str | None,
) -> Transaction:
    """Approve or reject, whichever status is passed in.

    Locks the company first — the same order every other money operation
    uses — so concurrent decisions queue up instead of deadlocking.
    """
    verb = "approve" if status is TransactionStatus.APPROVED else "reject"
    if not actor.is_admin:
        raise PermissionDenied(
            f"{actor.name} has role '{actor.role.value}' and cannot {verb} expenses."
        )

    get_company(session, _company_id_for(session, transaction_id), lock=True)
    txn = _claim_pending_expense(session, transaction_id)

    txn.status = status
    txn.decided_by = actor.name
    txn.decided_at = utcnow()
    txn.decision_note = note
    session.flush()
    logger.info("Transaction %s %sd by %s", transaction_id, verb, actor.name)
    return txn


def approve_expense(
    session: Session, transaction_id: int, actor: Actor, *, note: str | None = None
) -> Transaction:
    """Approve a pending expense. Admins only."""
    return _decide_expense(
        session, transaction_id, actor, TransactionStatus.APPROVED, note
    )


def reject_expense(
    session: Session, transaction_id: int, actor: Actor, *, note: str | None = None
) -> Transaction:
    """Reject a pending expense, releasing the funds it was holding."""
    return _decide_expense(
        session, transaction_id, actor, TransactionStatus.REJECTED, note
    )


def reverse_transaction(
    session: Session, transaction_id: int, actor: Actor, *, reason: str
) -> Transaction:
    """Undo an approved transaction that was entered wrongly. Admins only.

    The row is kept exactly as it was and marked reversed, so it stops
    counting towards balances without disappearing from the record — an
    accounting ledger should show that a correction happened, not pretend
    the original entry never existed.

    Reversing recorded income can leave the balance negative, when the money
    had already been spent on the strength of an entry that turned out to be
    wrong. That is allowed: it is the true position, and further spending
    stays blocked until the balance recovers.
    """
    if not actor.is_admin:
        raise PermissionDenied(
            f"{actor.name} has role '{actor.role.value}' and cannot reverse transactions."
        )
    reason = (reason or "").strip()
    if not reason:
        raise InvalidState("A reversal needs a reason — it stays on the record.")

    get_company(session, _company_id_for(session, transaction_id), lock=True)

    txn = session.execute(
        select(Transaction)
        .where(Transaction.id == transaction_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if txn is None:
        raise NotFound(f"No transaction with id {transaction_id}.")
    if txn.status is not TransactionStatus.APPROVED:
        raise InvalidState(
            f"Only approved transactions can be reversed; transaction "
            f"{transaction_id} is {txn.status.value}. "
            "Use reject for one that is still awaiting approval."
        )

    txn.status = TransactionStatus.REVERSED
    txn.reversed_by = actor.name
    txn.reversed_at = utcnow()
    txn.reversal_reason = reason
    session.flush()
    logger.info("Transaction %s reversed by %s: %s", transaction_id, actor.name, reason)
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
    since: date | None = None,
    until: date | None = None,
    limit: int = 100,
    offset: int = 0,
) -> Sequence[Transaction]:
    """Newest first. Dates filter on when the money moved, not when it was
    typed in."""
    stmt = select(Transaction).where(Transaction.company_id == company_id)
    if status is not None:
        stmt = stmt.where(Transaction.status == status)
    if type is not None:
        stmt = stmt.where(Transaction.type == type)
    if since is not None:
        stmt = stmt.where(Transaction.occurred_on >= since)
    if until is not None:
        stmt = stmt.where(Transaction.occurred_on <= until)

    stmt = stmt.order_by(
        Transaction.occurred_on.desc(),
        Transaction.created_at.desc(),
        Transaction.id.desc(),
    )
    stmt = stmt.limit(max(1, min(limit, 1000))).offset(max(0, offset))
    return session.execute(stmt).scalars().all()


def count_transactions(
    session: Session,
    company_id: int,
    *,
    status: TransactionStatus | None = None,
) -> int:
    """How many transactions match, so a caller can page through them."""
    stmt = select(func.count(Transaction.id)).where(
        Transaction.company_id == company_id
    )
    if status is not None:
        stmt = stmt.where(Transaction.status == status)
    return int(session.execute(stmt).scalar_one())


def get_report(
    session: Session,
    company_id: int,
    *,
    since: date | None = None,
    until: date | None = None,
) -> dict:
    """A company's position, optionally narrowed to a period.

    When a period is given, the income and expense figures cover that period
    while the balance still reflects everything — a month's spending is a
    different question from how much money the company actually has.
    """
    company = get_company(session, company_id)
    overall = get_balances(session, company_id)
    pending = list_transactions(
        session, company_id, status=TransactionStatus.PENDING, limit=50
    )

    report = {
        "company_id": company.id,
        "company": company.name,
        "owner": company.owner_name,
        "currency": company.currency,
        **overall.as_dict(),
        # Counted over the whole ledger, not over the capped display list
        # above, so a backlog of more than 50 isn't reported as exactly 50.
        "pending_count": count_transactions(
            session, company_id, status=TransactionStatus.PENDING
        ),
        "pending_transactions": [t.as_dict() for t in pending],
    }

    if since is not None or until is not None:
        period = get_balances(session, company_id, since=since, until=until)
        report["period"] = {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "income": str(period.income),
            "expense": str(period.expense),
            "net": str(period.income - period.expense),
        }
    return report


CSV_COLUMNS = (
    "id",
    "occurred_on",
    "type",
    "status",
    "amount",
    "currency",
    "category",
    "description",
    "created_by",
    "created_at",
    "decided_by",
    "decided_at",
    "reversed_by",
    "reversal_reason",
)


def export_csv(
    session: Session,
    company_id: int,
    *,
    since: date | None = None,
    until: date | None = None,
) -> str:
    """The ledger as CSV, oldest first — the order an accountant expects."""
    company = get_company(session, company_id)
    transactions = list_transactions(
        session, company_id, since=since, until=until, limit=1000
    )

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for txn in reversed(transactions):
        row = txn.as_dict()
        row["currency"] = company.currency
        writer.writerow({column: row.get(column, "") for column in CSV_COLUMNS})
    return buffer.getvalue()
