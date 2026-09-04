"""Database models.

Money is stored as a ledger of individual transactions, not as running
totals. Balances are always derived by summing the ledger, so they can
never drift from reality, and concurrent writes cannot overwrite each
other's totals.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    """Who is performing an action.

    NOTE: this is a workflow convention, not a security boundary. Callers
    state their own role, so it describes intent rather than enforcing
    identity. See the security section of the README.
    """

    ADMIN = "admin"
    REGULAR_USER = "regular_user"
    OWNER = "owner"


class TransactionType(str, enum.Enum):
    INCOME = "income"
    EXPENSE = "expense"


class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Actor:
    """The person performing an action. Not persisted — see Role."""

    name: str
    role: Role = Role.REGULAR_USER

    @property
    def is_admin(self) -> bool:
        return self.role in (Role.ADMIN, Role.OWNER)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    owner_name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    transactions: Mapped[list[Transaction]] = relationship(
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

    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )

    decided_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

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
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "decision_note": self.decision_note,
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
