from decimal import Decimal

import pytest

from account_manager.models import (
    Actor,
    Balances,
    Role,
    Transaction,
    TransactionStatus,
    TransactionType,
)


def test_balance_is_income_minus_approved_expense():
    balances = Balances(
        income=Decimal("1000"), expense=Decimal("250"), pending_expense=Decimal("0")
    )
    assert balances.balance == Decimal("750")


def test_available_excludes_money_awaiting_approval():
    balances = Balances(
        income=Decimal("1000"), expense=Decimal("250"), pending_expense=Decimal("100")
    )
    assert balances.balance == Decimal("750")
    assert balances.available == Decimal("650")


def test_balances_serialise_as_strings():
    balances = Balances(
        income=Decimal("10.00"), expense=Decimal("2.50"), pending_expense=Decimal("0")
    )
    assert balances.as_dict()["balance"] == "7.50"


@pytest.mark.parametrize(
    ("role", "expected"),
    [(Role.ADMIN, True), (Role.OWNER, True), (Role.REGULAR_USER, False)],
)
def test_only_admins_and_owners_count_as_admin(role, expected):
    assert Actor(name="X", role=role).is_admin is expected


def test_actor_defaults_to_the_least_privileged_role():
    assert Actor(name="X").role is Role.REGULAR_USER


def test_timestamps_are_naive_so_they_compare_across_databases():
    """MySQL and SQLite both return naive datetimes; mixing naive and
    aware values raises TypeError on comparison."""
    from account_manager.models import utcnow

    assert utcnow().tzinfo is None


def test_transaction_serialises_enum_values_not_names():
    txn = Transaction(
        company_id=1,
        type=TransactionType.EXPENSE,
        status=TransactionStatus.PENDING,
        amount=Decimal("12.34"),
        created_by="Raza",
    )
    as_dict = txn.as_dict()
    assert as_dict["type"] == "expense"
    assert as_dict["status"] == "pending"
    assert as_dict["amount"] == "12.34"
