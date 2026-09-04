"""Initial ledger schema, migrating any pre-ledger data.

Fresh installs simply get the `companies` and `transactions` tables.

Installs upgrading from the original version have `Company` and `Account`
tables holding running totals instead of individual transactions. Those
totals are converted into one opening transaction each (income, spent,
and awaiting-approval), so no money is lost in the upgrade, and the old
tables are then dropped.

Revision ID: 0001_initial_ledger
Revises:
Create Date: 2026-09-04
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0001_initial_ledger"
down_revision = None
branch_labels = None
depends_on = None

TXN_TYPE = sa.Enum("income", "expense", name="transactiontype", native_enum=False, length=20)
TXN_STATUS = sa.Enum(
    "pending", "approved", "rejected", name="transactionstatus", native_enum=False, length=20
)

# What the original version created, before this project used migrations.
LEGACY_COMPANY = "Company"
LEGACY_ACCOUNT = "Account"


def _table_names(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    existing = _table_names(bind)

    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("owner_name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("type", TXN_TYPE, nullable=False),
        sa.Column("status", TXN_STATUS, nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("category", sa.String(length=60), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_by", sa.String(length=120), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transactions_company_id", "transactions", ["company_id"])
    op.create_index("ix_transactions_status", "transactions", ["status"])
    op.create_index("ix_transactions_created_at", "transactions", ["created_at"])

    if LEGACY_COMPANY in existing:
        _migrate_legacy_data(bind, existing)


def _migrate_legacy_data(bind, existing: set[str]) -> None:
    """Copy pre-ledger companies and running totals into the new tables."""
    # Naive UTC, matching how the application stores timestamps (see
    # account_manager.models.utcnow).
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    companies = bind.execute(
        sa.text(f"SELECT id, name, owner_name FROM {LEGACY_COMPANY}")  # noqa: S608
    ).fetchall()

    for company_id, name, owner_name in companies:
        bind.execute(
            sa.text(
                "INSERT INTO companies (id, name, owner_name, created_at) "
                "VALUES (:id, :name, :owner_name, :created_at)"
            ),
            {
                "id": company_id,
                "name": name,
                "owner_name": owner_name,
                "created_at": now,
            },
        )

    if LEGACY_ACCOUNT in existing:
        accounts = bind.execute(
            sa.text(
                "SELECT company_id, income, expense, pending_expense "  # noqa: S608
                f"FROM {LEGACY_ACCOUNT}"
            )
        ).fetchall()

        for company_id, income, expense, pending in accounts:
            opening = (
                ("income", "approved", income, "Opening balance imported from totals"),
                ("expense", "approved", expense, "Opening spend imported from totals"),
                (
                    "expense",
                    "pending",
                    pending,
                    "Expense awaiting approval, imported from totals",
                ),
            )
            for txn_type, status, amount, description in opening:
                if amount is None or amount <= 0:
                    continue
                bind.execute(
                    sa.text(
                        "INSERT INTO transactions "
                        "(company_id, type, status, amount, description, "
                        " created_by, created_at, decided_by, decided_at) "
                        "VALUES (:company_id, :type, :status, :amount, :description, "
                        " :created_by, :created_at, :decided_by, :decided_at)"
                    ),
                    {
                        "company_id": company_id,
                        "type": txn_type,
                        "status": status,
                        "amount": amount,
                        "description": description,
                        "created_by": "migration",
                        "created_at": now,
                        "decided_by": "migration" if status == "approved" else None,
                        "decided_at": now if status == "approved" else None,
                    },
                )
        op.drop_table(LEGACY_ACCOUNT)

    op.drop_table(LEGACY_COMPANY)


def downgrade() -> None:
    op.drop_index("ix_transactions_created_at", table_name="transactions")
    op.drop_index("ix_transactions_status", table_name="transactions")
    op.drop_index("ix_transactions_company_id", table_name="transactions")
    op.drop_table("transactions")
    op.drop_table("companies")
