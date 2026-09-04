"""Tests for the business rules.

Several of these exist because the behaviour they check was previously
broken; each one is named after the problem it prevents coming back.
"""

from decimal import Decimal

import pytest

from account_manager import db, services
from account_manager.errors import (
    InsufficientFunds,
    InvalidAmount,
    InvalidState,
    NotFound,
    PermissionDenied,
)
from account_manager.models import Actor, Role, TransactionStatus, TransactionType


@pytest.fixture
def company(session):
    return services.create_company(session, "Acme", "Ahmed")


@pytest.fixture
def funded(session, company, admin):
    """A company holding 1000.00."""
    services.add_income(session, company.id, admin, "1000.00")
    return company


# ---------------------------------------------------------------- companies


def test_create_company(session):
    company = services.create_company(session, "Acme", "Ahmed")
    assert company.id is not None
    assert services.list_companies(session)[0].name == "Acme"


def test_create_company_rejects_blank_name(session):
    with pytest.raises(InvalidState):
        services.create_company(session, "   ", "Ahmed")


def test_get_missing_company_raises(session):
    with pytest.raises(NotFound):
        services.get_company(session, 999)


# ------------------------------------------------------------------- income


def test_admin_records_income(session, company, admin):
    txn = services.add_income(session, company.id, admin, "500.50")
    assert txn.type is TransactionType.INCOME
    assert txn.status is TransactionStatus.APPROVED
    assert services.get_balances(session, company.id).income == Decimal("500.50")


def test_regular_user_cannot_record_income(session, company, staff):
    """Regression: the permission check used to be bypassed by hardcoding admin."""
    with pytest.raises(PermissionDenied):
        services.add_income(session, company.id, staff, "500")


@pytest.mark.parametrize("bad", ["0", "-5", "abc", "", "1.234"])
def test_invalid_amounts_are_refused(session, company, admin, bad):
    with pytest.raises(InvalidAmount):
        services.add_income(session, company.id, admin, bad)


def test_float_amounts_do_not_lose_precision(session, company, admin):
    services.add_income(session, company.id, admin, 0.1)
    services.add_income(session, company.id, admin, 0.2)
    assert services.get_balances(session, company.id).income == Decimal("0.30")


# ------------------------------------------------------------------ expenses


def test_admin_expense_is_approved_immediately(session, funded, admin):
    txn = services.submit_expense(session, funded.id, admin, "250")
    assert txn.status is TransactionStatus.APPROVED
    balances = services.get_balances(session, funded.id)
    assert balances.expense == Decimal("250.00")
    assert balances.balance == Decimal("750.00")


def test_admin_cannot_spend_more_than_the_balance(session, funded, admin):
    """Regression: an approved admin previously had no spending limit at all,
    so the balance could be driven arbitrarily negative."""
    with pytest.raises(InsufficientFunds):
        services.submit_expense(session, funded.id, admin, "999999")
    assert services.get_balances(session, funded.id).balance == Decimal("1000.00")


def test_regular_user_expense_waits_for_approval(session, funded, staff):
    txn = services.submit_expense(session, funded.id, staff, "100")
    assert txn.status is TransactionStatus.PENDING
    balances = services.get_balances(session, funded.id)
    assert balances.expense == Decimal("0")
    assert balances.pending_expense == Decimal("100.00")


def test_expense_is_checked_against_remaining_balance_not_gross_income(
    session, funded, admin, staff
):
    """Regression: the old check compared against gross income, so a user
    could commit far more than the company actually held."""
    services.submit_expense(session, funded.id, admin, "900")  # 100 left
    with pytest.raises(InsufficientFunds):
        services.submit_expense(session, funded.id, staff, "1000")


def test_pending_expense_reserves_funds(session, funded, staff):
    services.submit_expense(session, funded.id, staff, "1000")
    assert services.get_balances(session, funded.id).available == Decimal("0")
    with pytest.raises(InsufficientFunds):
        services.submit_expense(session, funded.id, staff, "1")


# ----------------------------------------------------------------- approvals


def test_approve_pending_expense(session, funded, staff, admin):
    """Regression: pending expenses used to have no approval path at all."""
    txn = services.submit_expense(session, funded.id, staff, "300")
    approved = services.approve_expense(session, txn.id, admin, note="fine")

    assert approved.status is TransactionStatus.APPROVED
    assert approved.decided_by == "Ahmed"
    assert approved.decided_at is not None
    balances = services.get_balances(session, funded.id)
    assert balances.expense == Decimal("300.00")
    assert balances.pending_expense == Decimal("0")
    assert balances.balance == Decimal("700.00")


def test_reject_releases_the_reserved_funds(session, funded, staff, admin):
    txn = services.submit_expense(session, funded.id, staff, "300")
    rejected = services.reject_expense(session, txn.id, admin, note="not needed")

    assert rejected.status is TransactionStatus.REJECTED
    balances = services.get_balances(session, funded.id)
    assert balances.expense == Decimal("0")
    assert balances.pending_expense == Decimal("0")
    assert balances.available == Decimal("1000.00")


def test_regular_user_cannot_approve(session, funded, staff):
    txn = services.submit_expense(session, funded.id, staff, "100")
    with pytest.raises(PermissionDenied):
        services.approve_expense(session, txn.id, staff)


def test_cannot_approve_the_same_expense_twice(session, funded, staff, admin):
    txn = services.submit_expense(session, funded.id, staff, "100")
    services.approve_expense(session, txn.id, admin)
    with pytest.raises(InvalidState):
        services.approve_expense(session, txn.id, admin)


def test_cannot_approve_an_income_transaction(session, funded, admin):
    income = services.list_transactions(
        session, funded.id, type=TransactionType.INCOME
    )[0]
    with pytest.raises(InvalidState):
        services.approve_expense(session, income.id, admin)


def test_approving_a_missing_transaction_raises(session, admin):
    with pytest.raises(NotFound):
        services.approve_expense(session, 4242, admin)


def test_owner_role_may_approve(session, funded, staff):
    txn = services.submit_expense(session, funded.id, staff, "100")
    owner = Actor(name="Ahmed", role=Role.OWNER)
    assert services.approve_expense(session, txn.id, owner).status is (
        TransactionStatus.APPROVED
    )


# -------------------------------------------------------- ledger correctness


def test_balances_are_derived_from_the_ledger(session, funded, admin, staff):
    services.submit_expense(session, funded.id, admin, "100")
    services.submit_expense(session, funded.id, staff, "50")
    rejected = services.submit_expense(session, funded.id, staff, "25")
    services.reject_expense(session, rejected.id, admin)

    balances = services.get_balances(session, funded.id)
    assert balances.income == Decimal("1000.00")
    assert balances.expense == Decimal("100.00")
    assert balances.pending_expense == Decimal("50.00")
    assert balances.balance == Decimal("900.00")
    assert balances.available == Decimal("850.00")


def test_rejected_transactions_do_not_affect_balances(session, funded, staff, admin):
    txn = services.submit_expense(session, funded.id, staff, "400")
    services.reject_expense(session, txn.id, admin)
    assert services.get_balances(session, funded.id).balance == Decimal("1000.00")


def test_transaction_history_is_recorded(session, funded, staff):
    services.submit_expense(
        session, funded.id, staff, "75", description="Taxi", category="travel"
    )
    transactions = services.list_transactions(session, funded.id)
    assert len(transactions) == 2  # the income, plus this expense
    expense = transactions[0]
    assert expense.description == "Taxi"
    assert expense.category == "travel"
    assert expense.created_by == "Raza"
    assert expense.created_at is not None


def test_list_transactions_filters_by_status(session, funded, staff):
    services.submit_expense(session, funded.id, staff, "10")
    pending = services.list_transactions(
        session, funded.id, status=TransactionStatus.PENDING
    )
    assert len(pending) == 1
    assert pending[0].amount == Decimal("10.00")


# --------------------------------------------------- transactions and safety


def test_failed_operation_rolls_back_completely(database):
    """Regression: creating a company used to commit in two steps, so a
    failure halfway left an unusable half-created record behind."""
    with pytest.raises(InvalidAmount):
        with db.session_scope() as session:
            company = services.create_company(session, "Ghost", "Nobody")
            services.add_income(
                session, company.id, Actor("Ahmed", Role.ADMIN), "not-a-number"
            )

    with db.session_scope() as session:
        assert services.list_companies(session) == []


def test_a_stale_reader_cannot_clobber_another_write(database):
    """Regression: balances were stored as totals and written back
    absolutely, so a session holding a stale total would silently erase a
    write another session had already committed. The ledger appends rows
    instead, so both writes survive."""
    admin = Actor("Ahmed", Role.ADMIN)
    with db.session_scope() as setup:
        company_id = services.create_company(setup, "Acme", "Ahmed").id

    with db.session_scope() as session_a:
        services.get_balances(session_a, company_id)  # A reads first

        with db.session_scope() as session_b:  # B writes and commits
            services.add_income(session_b, company_id, admin, "500")

        services.add_income(session_a, company_id, admin, "200")  # A writes after

    with db.session_scope() as check:
        assert services.get_balances(check, company_id).income == Decimal("700.00")
