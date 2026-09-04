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
