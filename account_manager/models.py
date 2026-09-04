"""Database models.

Money is stored as a ledger of individual transactions, not as running
totals. Balances are always derived by summing the ledger, so they can
never drift from reality, and concurrent writes cannot overwrite each
other's totals.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

DEFAULT_CURRENCY = "USD"


def utcnow() -> datetime:
    """The current UTC time, without a timezone attached.

    MySQL's DATETIME and SQLite both drop timezone information, so a value
    read back from either is naive. Storing naive UTC everywhere means
    comparing a stored timestamp against a fresh one behaves the same on
    every supported database, instead of working on one and raising
    "can't compare offset-naive and offset-aware datetimes" on another.
    Everything in this project is UTC.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utctoday() -> date:
    """Today's date in UTC."""
    return datetime.now(timezone.utc).date()


def _enum_column(enum_cls: type[enum.Enum]) -> Enum:
    """Store the enum's *value* ("income"), not its name ("INCOME").

    Keeps the database readable in plain SQL and matching the strings the
    CLI and MCP tools use.
    """
    return Enum(
        enum_cls,
        native_enum=False,
        length=20,
        values_callable=lambda cls: [member.value for member in cls],
    )


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    """What a user may do within one company.

    Held on their CompanyMember row and read from there, so a caller cannot
    claim a role they have not been granted.
    """

    #: May record income, approve and reject requests, and reverse mistakes.
    ADMIN = "admin"
    #: The same authority as admin; a separate name for the person who owns
    #: the business rather than administers the books.
    OWNER = "owner"
    #: May request spending, which then waits for an admin.
    REGULAR_USER = "regular_user"


class TransactionType(str, enum.Enum):
    INCOME = "income"
    EXPENSE = "expense"


class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    #: A previously approved transaction that turned out to be wrong. The row
    #: is kept exactly as it was and simply stops counting towards balances,
    #: so the books can be corrected without erasing what was recorded.
    REVERSED = "reversed"


#: Statuses whose money is actually committed.
COUNTED_STATUSES = (TransactionStatus.APPROVED, TransactionStatus.PENDING)


@dataclass(frozen=True)
class Actor:
    """Who is performing an action, and with what authority.

    Built by the auth layer from an authenticated user and their membership
    of the company being acted on — never from something the caller typed.
    """

    name: str
    role: Role = Role.REGULAR_USER
    user_id: int | None = None

    @property
    def is_admin(self) -> bool:
        return self.role in (Role.ADMIN, Role.OWNER)


class User(Base):
    """Someone who can log in."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Can manage users and reaches every company as an admin. The person
    #: who set the install up.
    is_superuser: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa.false(), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sa.true(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    memberships: Mapped[list[CompanyMember]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    tokens: Mapped[list[ApiToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r}>"


class CompanyMember(Base):
    """Which companies a user can see, and what they may do in each.

    Access is per company rather than per install, so one deployment can
    hold several companies without everyone seeing all of them.
    """

    __tablename__ = "company_members"

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[Role] = mapped_column(_enum_column(Role), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="memberships")
    company: Mapped[Company] = relationship(back_populates="members")


class ApiToken(Base):
    """A logged-in session for the CLI, or a credential for the MCP server.

    Only the hash is stored, so a stolen database yields no usable tokens.
    """

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="cli")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="tokens")


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    owner_name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: ISO 4217 code, for display only — this project does not convert
    #: between currencies, so one company keeps one currency.
    currency: Mapped[str] = mapped_column(
        String(3), default=DEFAULT_CURRENCY, server_default=DEFAULT_CURRENCY, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    members: Mapped[list[CompanyMember]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Company id={self.id} name={self.name!r}>"


class Transaction(Base):
    """One movement of money: income received, or an expense requested."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[TransactionType] = mapped_column(
        _enum_column(TransactionType), nullable=False
    )
    status: Mapped[TransactionStatus] = mapped_column(
        _enum_column(TransactionStatus), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: The date the money actually moved, which is not the date somebody got
    #: round to typing it in. Reporting uses this; created_at is the audit
    #: record of when the entry was made.
    occurred_on: Mapped[date] = mapped_column(Date, default=utctoday, nullable=False, index=True)

    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )

    decided_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Set when a mistake is corrected. The original row keeps its own
    #: decided_by/decided_at, so both the approval and the correction stay
    #: on the record.
    reversed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reversal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    company: Mapped[Company] = relationship(back_populates="transactions")

    def __repr__(self) -> str:
        return (
            f"<Transaction id={self.id} {self.type.value} "
            f"{self.amount} {self.status.value}>"
        )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "company_id": self.company_id,
            "type": self.type.value,
            "status": self.status.value,
            "amount": str(self.amount),
            "category": self.category,
            "description": self.description,
            "occurred_on": self.occurred_on.isoformat() if self.occurred_on else None,
            "created_by": self.created_by,
            # Stored naive but always UTC; the Z makes that explicit to callers.
            "created_at": f"{self.created_at.isoformat()}Z" if self.created_at else None,
            "decided_by": self.decided_by,
            "decided_at": f"{self.decided_at.isoformat()}Z" if self.decided_at else None,
            "decision_note": self.decision_note,
            "reversed_by": self.reversed_by,
            "reversed_at": f"{self.reversed_at.isoformat()}Z" if self.reversed_at else None,
            "reversal_reason": self.reversal_reason,
        }


@dataclass(frozen=True)
class Balances:
    """Derived from the ledger — never stored."""

    income: Decimal
    expense: Decimal
    pending_expense: Decimal

    @property
    def balance(self) -> Decimal:
        """Money actually held: approved income minus approved expense."""
        return self.income - self.expense

    @property
    def available(self) -> Decimal:
        """Money free to commit: balance minus what is awaiting approval."""
        return self.balance - self.pending_expense

    def as_dict(self) -> dict:
        return {
            "income": str(self.income),
            "expense": str(self.expense),
            "pending_expense": str(self.pending_expense),
            "balance": str(self.balance),
            "available": str(self.available),
        }
