"""Tests for the command line interface."""

import pytest

from account_manager.cli import main


def run(*argv: str) -> int:
    return main(list(argv))


def test_create_company_and_report(database, capsys):
    assert run("company", "create", "--name", "Acme", "--owner", "Ahmed") == 0
    assert run("income", "add", "--company", "1", "--amount", "1000", "--by", "Ahmed") == 0
    assert run("report", "--company", "1") == 0

    output = capsys.readouterr().out
    assert "Acme" in output
    assert "1000.00" in output


def test_expense_needs_approval_then_is_approved(database, capsys):
    run("company", "create", "--name", "Acme", "--owner", "Ahmed")
    run("income", "add", "--company", "1", "--amount", "500", "--by", "Ahmed")
    run(
        "expense", "submit", "--company", "1", "--amount", "200",
        "--by", "Raza", "--role", "regular_user",
    )
    capsys.readouterr()

    assert run("report", "--company", "1") == 0
    assert "Awaiting approval" in capsys.readouterr().out

    assert run("expense", "approve", "--id", "2", "--by", "Ahmed") == 0
    assert "approved" in capsys.readouterr().out


def test_overspending_exits_nonzero_with_a_readable_error(database, capsys):
    run("company", "create", "--name", "Acme", "--owner", "Ahmed")
    run("income", "add", "--company", "1", "--amount", "100", "--by", "Ahmed")

    assert run(
        "expense", "submit", "--company", "1", "--amount", "99999",
        "--by", "Ahmed", "--role", "admin",
    ) == 1
    assert "exceeds the available balance" in capsys.readouterr().err


def test_regular_user_cannot_approve_via_cli(database, capsys):
    run("company", "create", "--name", "Acme", "--owner", "Ahmed")
    run("income", "add", "--company", "1", "--amount", "500", "--by", "Ahmed")
    run(
        "expense", "submit", "--company", "1", "--amount", "50",
        "--by", "Raza", "--role", "regular_user",
    )
    capsys.readouterr()

    assert run(
        "expense", "approve", "--id", "2", "--by", "Raza", "--role", "regular_user"
    ) == 1
    assert "cannot approve" in capsys.readouterr().err


def test_unknown_company_exits_nonzero(database, capsys):
    assert run("report", "--company", "999") == 1
    assert "No company with id 999" in capsys.readouterr().err


def test_missing_required_argument_is_rejected(database):
    with pytest.raises(SystemExit):
        run("company", "create", "--name", "Acme")  # no --owner


def test_reverse_corrects_a_mistake(database, capsys):
    run("company", "create", "--name", "Acme", "--owner", "Ahmed")
    run("income", "add", "--company", "1", "--amount", "1000", "--by", "Ahmed")
    run("expense", "submit", "--company", "1", "--amount", "300",
        "--by", "Ahmed", "--role", "admin")
    capsys.readouterr()

    assert run("expense", "reverse", "--id", "2", "--by", "Ahmed", "--reason", "wrong amount") == 0
    assert "reversed" in capsys.readouterr().out

    run("report", "--company", "1")
    assert "1000.00" in capsys.readouterr().out


def test_reverse_requires_a_reason(database):
    with pytest.raises(SystemExit):
        run("expense", "reverse", "--id", "1", "--by", "Ahmed")


def test_backdated_entry_and_period_report(database, capsys):
    run("company", "create", "--name", "Acme", "--owner", "Ahmed")
    run("income", "add", "--company", "1", "--amount", "500", "--by", "Ahmed",
        "--date", "2026-01-15")
    run("income", "add", "--company", "1", "--amount", "700", "--by", "Ahmed",
        "--date", "2026-06-15")
    capsys.readouterr()

    run("report", "--company", "1", "--since", "2026-01-01", "--until", "2026-01-31")
    output = capsys.readouterr().out
    assert "Period 2026-01-01 to 2026-01-31" in output
    assert "500.00" in output
    assert "1200.00" in output  # the overall balance still counts both


def test_a_bad_date_is_rejected(database):
    with pytest.raises(SystemExit):
        run("report", "--company", "1", "--since", "15-01-2026")


def test_export_writes_a_csv_file(database, capsys, tmp_path):
    run("company", "create", "--name", "Acme", "--owner", "Ahmed", "--currency", "PKR")
    run("income", "add", "--company", "1", "--amount", "250", "--by", "Ahmed")
    capsys.readouterr()

    target = tmp_path / "ledger.csv"
    assert run("export", "--company", "1", "--output", str(target)) == 0
    content = target.read_text()
    assert "id,occurred_on,type,status,amount,currency" in content
    assert "250.00" in content
    assert "PKR" in content


def test_transactions_pagination_flags(database, capsys):
    run("company", "create", "--name", "Acme", "--owner", "Ahmed")
    run("income", "add", "--company", "1", "--amount", "1000", "--by", "Ahmed")
    for amount in ("10", "20", "30"):
        run("expense", "submit", "--company", "1", "--amount", amount,
            "--by", "Ahmed", "--role", "admin")
    capsys.readouterr()

    run("transactions", "--company", "1", "--limit", "2")
    assert len(capsys.readouterr().out.strip().splitlines()) == 2
