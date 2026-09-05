"""Tests for dates, currency, reversal, period reporting and CSV export."""

import csv
import io
from datetime import date
from decimal import Decimal

import pytest

from account_manager import services
from account_manager.errors import (
    InsufficientFunds,
    InvalidState,
    NotFound,
    PermissionDenied,
)
from account_manager.models import TransactionStatus, TransactionType, utctoday


@pytest.fixture
def company(session):
    return services.create_company(session, "Acme", "Ahmed")


@pytest.fixture
def funded(session, company, admin):
    services.add_income(session, company.id, admin, "1000.00")
    return company


# ------------------------------------------------------------------ currency


def test_company_defaults_to_usd(session):
    assert services.create_company(session, "Acme", "Ahmed").currency == "USD"


def test_currency_is_stored_uppercase(session):
    company = services.create_company(session, "Acme", "Ahmed", currency="pkr")
    assert company.currency == "PKR"


@pytest.mark.parametrize("bad", ["US", "DOLLAR", "12", "U$D"])
def test_invalid_currency_is_refused(session, bad):
    with pytest.raises(InvalidState):
        services.create_company(session, "Acme", "Ahmed", currency=bad)


def test_an_empty_currency_means_the_default(session):
    """Empty is treated as 'not specified' rather than as an error."""
    assert services.create_company(session, "Acme", "Ahmed", currency="").currency == "USD"


# --------------------------------------------------------------------- dates


def test_transactions_default_to_today(session, funded, admin):
    txn = services.submit_expense(session, funded.id, admin, "10")
    assert txn.occurred_on == utctoday()


def test_a_transaction_can_be_dated_in_the_past(session, funded, admin):
    """Entering last week's receipt today."""
    last_week = date(2026, 1, 5)
    txn = services.submit_expense(
        session, funded.id, admin, "10", occurred_on=last_week
    )
    assert txn.occurred_on == last_week
    # created_at still records when it was actually entered.
    assert txn.created_at.date() != last_week


def test_transactions_can_be_filtered_by_date(session, funded, admin):
    services.submit_expense(session, funded.id, admin, "10", occurred_on=date(2026, 1, 10))
    services.submit_expense(session, funded.id, admin, "20", occurred_on=date(2026, 2, 10))
    services.submit_expense(session, funded.id, admin, "30", occurred_on=date(2026, 3, 10))

    february = services.list_transactions(
        session, funded.id, since=date(2026, 2, 1), until=date(2026, 2, 28)
    )
    assert [t.amount for t in february] == [Decimal("20.00")]


def test_balances_can_be_scoped_to_a_period(session, company, admin):
    services.add_income(session, company.id, admin, "500", occurred_on=date(2026, 1, 15))
    services.add_income(session, company.id, admin, "300", occurred_on=date(2026, 2, 15))

    january = services.get_balances(
        session, company.id, since=date(2026, 1, 1), until=date(2026, 1, 31)
    )
    assert january.income == Decimal("500.00")
    # The overall position still counts everything.
    assert services.get_balances(session, company.id).income == Decimal("800.00")


def test_report_includes_a_period_section_when_dates_are_given(session, company, admin):
    services.add_income(session, company.id, admin, "500", occurred_on=date(2026, 1, 15))
    services.submit_expense(
        session, company.id, admin, "200", occurred_on=date(2026, 1, 20)
    )

    report = services.get_report(
        session, company.id, since=date(2026, 1, 1), until=date(2026, 1, 31)
    )
    assert report["period"]["income"] == "500.00"
    assert report["period"]["expense"] == "200.00"
    assert report["period"]["net"] == "300.00"


def test_report_has_no_period_section_without_dates(session, funded):
    assert "period" not in services.get_report(session, funded.id)


# ----------------------------------------------------------------- reversal


def test_reversing_an_approved_expense_returns_the_money(session, funded, admin):
    txn = services.submit_expense(session, funded.id, admin, "300")
    assert services.get_balances(session, funded.id).balance == Decimal("700.00")

    services.reverse_transaction(session, txn.id, admin, reason="wrong amount")

    balances = services.get_balances(session, funded.id)
    assert balances.expense == Decimal("0")
    assert balances.balance == Decimal("1000.00")


def test_a_reversed_transaction_is_kept_not_deleted(session, funded, admin):
    txn = services.submit_expense(session, funded.id, admin, "300")
    services.reverse_transaction(session, txn.id, admin, reason="duplicate entry")

    stored = services.list_transactions(session, funded.id, type=TransactionType.EXPENSE)
    assert len(stored) == 1
    assert stored[0].status is TransactionStatus.REVERSED
    assert stored[0].reversal_reason == "duplicate entry"
    assert stored[0].reversed_by == "Ahmed"
    assert stored[0].reversed_at is not None
    # The original approval is still on the record.
    assert stored[0].decided_by == "Ahmed"


def test_a_reversal_needs_a_reason(session, funded, admin):
    txn = services.submit_expense(session, funded.id, admin, "100")
    with pytest.raises(InvalidState, match="needs a reason"):
        services.reverse_transaction(session, txn.id, admin, reason="   ")


def test_only_admins_can_reverse(session, funded, admin, staff):
    txn = services.submit_expense(session, funded.id, admin, "100")
    with pytest.raises(PermissionDenied):
        services.reverse_transaction(session, txn.id, staff, reason="nope")


def test_a_pending_expense_cannot_be_reversed(session, funded, staff, admin):
    """Rejecting is the right operation for something not yet approved."""
    txn = services.submit_expense(session, funded.id, staff, "100")
    with pytest.raises(InvalidState, match="Use reject"):
        services.reverse_transaction(session, txn.id, admin, reason="changed mind")


def test_a_transaction_cannot_be_reversed_twice(session, funded, admin):
    txn = services.submit_expense(session, funded.id, admin, "100")
    services.reverse_transaction(session, txn.id, admin, reason="first")
    with pytest.raises(InvalidState):
        services.reverse_transaction(session, txn.id, admin, reason="second")


def test_reversing_a_missing_transaction_raises(session, admin):
    with pytest.raises(NotFound):
        services.reverse_transaction(session, 4242, admin, reason="x")


def test_reversing_income_can_leave_the_balance_negative(session, company, admin):
    """If money was spent on the strength of an entry that turns out to be
    wrong, the true position is negative and further spending is blocked."""
    income = services.add_income(session, company.id, admin, "1000")
    services.submit_expense(session, company.id, admin, "900")

    services.reverse_transaction(session, income.id, admin, reason="entered twice")

    balances = services.get_balances(session, company.id)
    assert balances.balance == Decimal("-900.00")
    with pytest.raises(InsufficientFunds):
        services.submit_expense(session, company.id, admin, "1")


def test_reversed_transactions_are_excluded_from_reports(session, funded, admin):
    txn = services.submit_expense(session, funded.id, admin, "400")
    services.reverse_transaction(session, txn.id, admin, reason="mistake")
    assert services.get_report(session, funded.id)["expense"] == "0"


# ---------------------------------------------------------------- pagination


def test_pagination_walks_the_ledger(session, funded, admin):
    for i in range(1, 6):
        services.submit_expense(session, funded.id, admin, str(i * 10))

    first = services.list_transactions(session, funded.id, limit=2, offset=0)
    second = services.list_transactions(session, funded.id, limit=2, offset=2)
    assert len(first) == len(second) == 2
    assert {t.id for t in first}.isdisjoint({t.id for t in second})


def test_counting_transactions(session, funded, staff, admin):
    services.submit_expense(session, funded.id, staff, "10")
    services.submit_expense(session, funded.id, admin, "20")
    assert services.count_transactions(session, funded.id) == 3  # + the income
    assert (
        services.count_transactions(
            session, funded.id, status=TransactionStatus.PENDING
        )
        == 1
    )


# --------------------------------------------------------------- csv export


def test_csv_export_has_a_row_per_transaction(session, funded, admin):
    services.submit_expense(
        session, funded.id, admin, "50", description="Taxi", category="travel"
    )
    rows = list(csv.DictReader(io.StringIO(services.export_csv(session, funded.id))))

    assert len(rows) == 2  # the income and the expense
    expense = [r for r in rows if r["type"] == "expense"][0]
    assert expense["amount"] == "50.00"
    assert expense["description"] == "Taxi"
    assert expense["category"] == "travel"
    assert expense["currency"] == "USD"


def test_csv_export_is_oldest_first(session, funded, admin):
    services.submit_expense(session, funded.id, admin, "10", occurred_on=date(2026, 3, 1))
    services.submit_expense(session, funded.id, admin, "20", occurred_on=date(2026, 1, 1))
    rows = list(csv.DictReader(io.StringIO(services.export_csv(session, funded.id))))
    dates = [r["occurred_on"] for r in rows]
    assert dates == sorted(dates)


def test_csv_export_respects_dates(session, company, admin):
    services.add_income(session, company.id, admin, "100", occurred_on=date(2026, 1, 5))
    services.add_income(session, company.id, admin, "200", occurred_on=date(2026, 6, 5))

    data = services.export_csv(
        session, company.id, since=date(2026, 1, 1), until=date(2026, 1, 31)
    )
    rows = list(csv.DictReader(io.StringIO(data)))
    assert [r["amount"] for r in rows] == ["100.00"]


def test_csv_export_of_an_empty_ledger_still_has_headers(session, company):
    rows = services.export_csv(session, company.id).splitlines()
    assert rows[0].startswith("id,occurred_on,type,status,amount,currency")
    assert len(rows) == 1


def test_pending_count_is_not_capped_by_the_display_limit(session, company, admin, staff):
    """Regression: pending_count came from a list capped at 50, so any
    backlog of 50 or more was reported as exactly 50."""
    services.add_income(session, company.id, admin, "100000")
    for _ in range(55):
        services.submit_expense(session, company.id, staff, "10")

    report = services.get_report(session, company.id)
    assert report["pending_count"] == 55
    assert len(report["pending_transactions"]) == 50  # display limit kept
