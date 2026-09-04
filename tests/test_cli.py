"""Tests for the command line interface."""

import pytest

from account_manager.cli import main


def run(*argv: str) -> int:
    return main(list(argv))


def test_create_company_and_report(superuser, capsys):
    assert run("company", "create", "--name", "Acme", "--owner", "Ahmed") == 0
    assert run("income", "add", "--company", "1", "--amount", "1000") == 0
    assert run("report", "--company", "1") == 0

    output = capsys.readouterr().out
    assert "Acme" in output
    assert "1000.00" in output


def test_an_admins_own_expense_applies_immediately(superuser, capsys):
    run("company", "create", "--name", "Acme", "--owner", "Ahmed")
    run("income", "add", "--company", "1", "--amount", "500")
    run("expense", "submit", "--company", "1", "--amount", "200")
    assert "approved" in capsys.readouterr().out


def test_a_staff_expense_waits_for_an_admin(superuser, staff_user, login_as, capsys):
    run("company", "create", "--name", "Acme", "--owner", "Ahmed")
    run("income", "add", "--company", "1", "--amount", "500")
    run("member", "add", "--company", "1", "--username", "raza",
        "--role", "regular_user")
    capsys.readouterr()

    login_as(staff_user)
    assert run("expense", "submit", "--company", "1", "--amount", "200") == 0
    assert "pending" in capsys.readouterr().out

    login_as(superuser)
    assert run("report", "--company", "1") == 0
    assert "Awaiting approval" in capsys.readouterr().out

    assert run("expense", "approve", "--id", "2") == 0
    assert "approved" in capsys.readouterr().out


def test_overspending_exits_nonzero_with_a_readable_error(superuser, capsys):
    run("company", "create", "--name", "Acme", "--owner", "Ahmed")
    run("income", "add", "--company", "1", "--amount", "100")

    assert run(
        "expense", "submit", "--company", "1", "--amount", "99999",
    ) == 1
    assert "exceeds the available balance" in capsys.readouterr().err


def test_a_staff_user_cannot_approve_their_own_request(
    superuser, staff_user, login_as, capsys
):
    run("company", "create", "--name", "Acme", "--owner", "Ahmed")
    run("income", "add", "--company", "1", "--amount", "500")
    run("member", "add", "--company", "1", "--username", "raza",
        "--role", "regular_user")

    login_as(staff_user)
    run("expense", "submit", "--company", "1", "--amount", "50")
    capsys.readouterr()

    assert run("expense", "approve", "--id", "2") == 1
    assert "cannot approve" in capsys.readouterr().err


def test_a_staff_user_cannot_record_income(superuser, staff_user, login_as, capsys):
    run("company", "create", "--name", "Acme", "--owner", "Ahmed")
    run("member", "add", "--company", "1", "--username", "raza",
        "--role", "regular_user")
    capsys.readouterr()

    login_as(staff_user)
    assert run("income", "add", "--company", "1", "--amount", "999") == 1
    assert "cannot record income" in capsys.readouterr().err


def test_a_company_you_are_not_a_member_of_is_not_visible(
    superuser, staff_user, login_as, capsys
):
    """Not 'forbidden' — invisible, so the install doesn't leak that other
    companies exist."""
    run("company", "create", "--name", "Acme", "--owner", "Ahmed")
    capsys.readouterr()

    login_as(staff_user)
    assert run("report", "--company", "1") == 1
    assert "No company with id 1" in capsys.readouterr().err

    assert run("company", "list") == 0
    assert "No companies yet" in capsys.readouterr().out


def test_commands_refuse_to_run_when_logged_out(database, capsys):
    assert run("company", "list") == 1
    assert "login" in capsys.readouterr().err


def test_unknown_company_exits_nonzero(superuser, capsys):
    assert run("report", "--company", "999") == 1
    assert "No company with id 999" in capsys.readouterr().err


def test_missing_required_argument_is_rejected(superuser):
    with pytest.raises(SystemExit):
        run("company", "create", "--name", "Acme")  # no --owner


def test_reverse_corrects_a_mistake(superuser, capsys):
    run("company", "create", "--name", "Acme", "--owner", "Ahmed")
    run("income", "add", "--company", "1", "--amount", "1000")
    run("expense", "submit", "--company", "1", "--amount", "300")
    capsys.readouterr()

    assert run("expense", "reverse", "--id", "2", "--reason", "wrong amount") == 0
    assert "reversed" in capsys.readouterr().out

    run("report", "--company", "1")
    assert "1000.00" in capsys.readouterr().out


def test_reverse_requires_a_reason(superuser):
    with pytest.raises(SystemExit):
        run("expense", "reverse", "--id", "1")


def test_backdated_entry_and_period_report(superuser, capsys):
    run("company", "create", "--name", "Acme", "--owner", "Ahmed")
    run("income", "add", "--company", "1", "--amount", "500",
        "--date", "2026-01-15")
    run("income", "add", "--company", "1", "--amount", "700",
        "--date", "2026-06-15")
    capsys.readouterr()

    run("report", "--company", "1", "--since", "2026-01-01", "--until", "2026-01-31")
    output = capsys.readouterr().out
    assert "Period 2026-01-01 to 2026-01-31" in output
    assert "500.00" in output
    assert "1200.00" in output  # the overall balance still counts both


def test_a_bad_date_is_rejected(superuser):
    with pytest.raises(SystemExit):
        run("report", "--company", "1", "--since", "15-01-2026")


def test_export_writes_a_csv_file(superuser, capsys, tmp_path):
    run("company", "create", "--name", "Acme", "--owner", "Ahmed", "--currency", "PKR")
    run("income", "add", "--company", "1", "--amount", "250")
    capsys.readouterr()

    target = tmp_path / "ledger.csv"
    assert run("export", "--company", "1", "--output", str(target)) == 0
    content = target.read_text()
    assert "id,occurred_on,type,status,amount,currency" in content
    assert "250.00" in content
    assert "PKR" in content


def test_transactions_pagination_flags(superuser, capsys):
    run("company", "create", "--name", "Acme", "--owner", "Ahmed")
    run("income", "add", "--company", "1", "--amount", "1000")
    for amount in ("10", "20", "30"):
        run("expense", "submit", "--company", "1", "--amount", amount)
    capsys.readouterr()

    run("transactions", "--company", "1", "--limit", "2")
    assert len(capsys.readouterr().out.strip().splitlines()) == 2
