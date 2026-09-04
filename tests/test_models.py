from decimal import Decimal

import pytest

from account_manager.models import Account, Company, ExpenseManager, Role, User


def test_company_is_owned_by_correct_owner():
    company = Company(name="Acme", owner_name="Ahmed")
    assert company.is_owned_by("Ahmed") is True
    assert company.is_owned_by("Someone Else") is False


def test_admin_can_add_income():
    account = Account(company_id=1)
    admin = User(name="Raza", role=Role.ADMIN)
    account.add_income(admin, Decimal("100"))
    assert account.income == Decimal("100")


def test_non_admin_cannot_add_income():
    account = Account(company_id=1)
    regular_user = User(name="Raza", role=Role.REGULAR_USER)
    with pytest.raises(PermissionError):
        account.add_income(regular_user, Decimal("100"))


def test_add_income_rejects_non_positive_amount():
    account = Account(company_id=1)
    admin = User(name="Raza", role=Role.ADMIN)
    with pytest.raises(ValueError):
        account.add_income(admin, Decimal("0"))


def test_regular_user_expense_within_income_goes_to_pending():
    account = Account(company_id=1, income=Decimal("1000"))
    regular_user = User(name="Raza", role=Role.REGULAR_USER)

    status = account.add_expense(regular_user, Decimal("500"))

    assert status == "pending_approval"
    assert account.pending_expense == Decimal("500")
    assert account.expense == Decimal("0")


def test_regular_user_expense_exceeding_income_is_rejected():
    account = Account(company_id=1, income=Decimal("100"))
    regular_user = User(name="Raza", role=Role.REGULAR_USER)

    status = account.add_expense(regular_user, Decimal("500"))

    assert status == "rejected: exceeds available income"
    assert account.pending_expense == Decimal("0")


def test_approved_admin_expense_is_applied_immediately():
    account = Account(company_id=1, income=Decimal("1000"))
    admin = User(name="Raza", role=Role.ADMIN, approved=True)

    status = account.add_expense(admin, Decimal("300"))

    assert status == "approved"
    assert account.expense == Decimal("300")
    assert account.pending_expense == Decimal("0")


def test_unapproved_admin_expense_goes_to_pending():
    account = Account(company_id=1, income=Decimal("1000"))
    admin = User(name="Raza", role=Role.ADMIN, approved=False)

    status = account.add_expense(admin, Decimal("300"))

    assert status == "pending_approval"
    assert account.pending_expense == Decimal("300")
    assert account.expense == Decimal("0")


def test_add_expense_rejects_non_positive_amount():
    account = Account(company_id=1, income=Decimal("1000"))
    admin = User(name="Raza", role=Role.ADMIN)
    with pytest.raises(ValueError):
        account.add_expense(admin, Decimal("0"))


def test_add_expense_rejects_unknown_role():
    account = Account(company_id=1, income=Decimal("1000"))
    stranger = User(name="Mallory", role="hacker")
    with pytest.raises(PermissionError):
        account.add_expense(stranger, Decimal("10"))


def test_calculate_budget():
    total, balance = ExpenseManager.calculate_budget(
        Decimal("1000"), [Decimal("200"), Decimal("50")]
    )
    assert total == Decimal("250")
    assert balance == Decimal("750")
