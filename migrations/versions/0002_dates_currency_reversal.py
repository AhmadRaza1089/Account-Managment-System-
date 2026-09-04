"""Transaction dates, per-company currency, and reversal.

Adds the date the money actually moved (separate from when somebody typed
it in), a currency code per company, and the fields recording who reversed
a mistaken entry and why.

Existing transactions get occurred_on backfilled from created_at, which is
the best available answer for entries made before the two were separated.

The status column needs no change: it is a plain VARCHAR, so the new
"reversed" value fits without altering the schema.

Revision ID: 0002_dates_currency_reversal
Revises: 0001_initial_ledger
Create Date: 2026-09-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_dates_currency_reversal"
down_revision = "0001_initial_ledger"
branch_labels = None
depends_on = None

DEFAULT_CURRENCY = "USD"


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            server_default=DEFAULT_CURRENCY,
        ),
    )

    # Added nullable so existing rows can be backfilled, then tightened.
    op.add_column("transactions", sa.Column("occurred_on", sa.Date(), nullable=True))
    op.execute(
        "UPDATE transactions SET occurred_on = DATE(created_at) "
        "WHERE occurred_on IS NULL"
    )
    with op.batch_alter_table("transactions") as batch:
        batch.alter_column("occurred_on", existing_type=sa.Date(), nullable=False)
    op.create_index("ix_transactions_occurred_on", "transactions", ["occurred_on"])

    op.add_column(
        "transactions", sa.Column("reversed_by", sa.String(length=120), nullable=True)
    )
    op.add_column("transactions", sa.Column("reversed_at", sa.DateTime(), nullable=True))
    op.add_column("transactions", sa.Column("reversal_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "reversal_reason")
    op.drop_column("transactions", "reversed_at")
    op.drop_column("transactions", "reversed_by")
    op.drop_index("ix_transactions_occurred_on", table_name="transactions")
    op.drop_column("transactions", "occurred_on")
    op.drop_column("companies", "currency")
