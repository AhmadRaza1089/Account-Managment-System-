"""Command line interface.

    account-manager init-db
    account-manager company create --name Acme --owner Ahmed
    account-manager company list
    account-manager income add --company 1 --amount 10000 --by Ahmed
    account-manager expense submit --company 1 --amount 500 --by Raza --role regular_user
    account-manager expense approve --id 2 --by Ahmed
    account-manager report --company 1
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__, services
from .ai.base import AIError
from .db import create_all, init_engine, session_scope
from .errors import AccountManagerError
from .models import Actor, Role, TransactionStatus


def _print_transaction(txn_dict: dict) -> None:
    print(
        f"#{txn_dict['id']:<5} {txn_dict['type']:<8} {txn_dict['amount']:>12} "
        f"{txn_dict['status']:<9} by {txn_dict['created_by']}"
        + (f" — {txn_dict['description']}" if txn_dict.get("description") else "")
    )


def _cmd_init_db(_args: argparse.Namespace) -> int:
    init_engine()
    create_all()
    print("Database ready.")
    return 0


def _cmd_company_create(args: argparse.Namespace) -> int:
    with session_scope() as session:
        company = services.create_company(session, args.name, args.owner)
        print(f"Created company #{company.id}: {company.name} (owner {company.owner_name})")
    return 0


def _cmd_company_list(_args: argparse.Namespace) -> int:
    with session_scope() as session:
        companies = services.list_companies(session)
        if not companies:
            print("No companies yet. Create one with: company create --name X --owner Y")
        for company in companies:
            print(f"#{company.id:<5} {company.name}  (owner {company.owner_name})")
    return 0


def _cmd_income_add(args: argparse.Namespace) -> int:
    with session_scope() as session:
        txn = services.add_income(
            session,
            args.company,
            Actor(name=args.by, role=Role(args.role)),
            args.amount,
            description=args.description,
            category=args.category,
        )
        balances = services.get_balances(session, args.company)
        _print_transaction(txn.as_dict())
        print(f"Balance: {balances.balance}  Available: {balances.available}")
    return 0


def _cmd_expense_submit(args: argparse.Namespace) -> int:
    with session_scope() as session:
        txn = services.submit_expense(
            session,
            args.company,
            Actor(name=args.by, role=Role(args.role)),
            args.amount,
            description=args.description,
            category=args.category,
        )
        balances = services.get_balances(session, args.company)
        _print_transaction(txn.as_dict())
        print(f"Balance: {balances.balance}  Available: {balances.available}")
    return 0


def _cmd_expense_approve(args: argparse.Namespace) -> int:
    with session_scope() as session:
        txn = services.approve_expense(
            session, args.id, Actor(name=args.by, role=Role(args.role)), note=args.note
        )
        _print_transaction(txn.as_dict())
    return 0


def _cmd_expense_reject(args: argparse.Namespace) -> int:
    with session_scope() as session:
        txn = services.reject_expense(
            session, args.id, Actor(name=args.by, role=Role(args.role)), note=args.note
        )
        _print_transaction(txn.as_dict())
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    with session_scope() as session:
        report = services.get_report(session, args.company)
    print(f"{report['company']} (owner {report['owner']})")
    print(f"  Income:            {report['income']:>14}")
    print(f"  Expenses:          {report['expense']:>14}")
    print(f"  Awaiting approval: {report['pending_expense']:>14}")
    print(f"  Balance:           {report['balance']:>14}")
    print(f"  Available:         {report['available']:>14}")
    if report["pending_transactions"]:
        print("\nAwaiting approval:")
        for txn in report["pending_transactions"]:
            _print_transaction(txn)
    return 0


def _cmd_ai_parse(args: argparse.Namespace) -> int:
    """Log a transaction written as an ordinary sentence."""
    from .ai import get_provider
    from .ai.extract import parse_transaction_text

    parsed = parse_transaction_text(args.text, get_provider())
    print(
        f"Understood: {parsed.type} {parsed.amount} "
        f"[{parsed.category}] {parsed.description}"
    )
    if args.dry_run:
        print("(dry run — nothing was recorded)")
        return 0

    actor = Actor(name=args.by, role=Role(args.role))
    with session_scope() as session:
        if parsed.type == "income":
            txn = services.add_income(
                session,
                args.company,
                actor,
                parsed.amount,
                description=parsed.description,
                category=parsed.category,
            )
        else:
            txn = services.submit_expense(
                session,
                args.company,
                actor,
                parsed.amount,
                description=parsed.description,
                category=parsed.category,
            )
        _print_transaction(txn.as_dict())
    return 0


def _cmd_ai_summary(args: argparse.Namespace) -> int:
    from .ai import get_provider
    from .ai.insights import summarise

    with session_scope() as session:
        print(summarise(session, args.company, get_provider()))
    return 0


def _cmd_ai_check(args: argparse.Namespace) -> int:
    """Statistical checks — works with no AI provider configured."""
    from .ai.anomalies import detect_anomalies

    with session_scope() as session:
        findings = detect_anomalies(session, args.company)

    if not findings:
        print("No anomalies found.")
        return 0
    for finding in findings:
        print(f"[{finding.severity:<6}] {finding.message}")
    return 0


def _cmd_transactions(args: argparse.Namespace) -> int:
    status = TransactionStatus(args.status) if args.status else None
    with session_scope() as session:
        transactions = services.list_transactions(
            session, args.company, status=status, limit=args.limit
        )
        if not transactions:
            print("No transactions found.")
        for txn in transactions:
            _print_transaction(txn.as_dict())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="account-manager", description="Track company income, expenses, approvals."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create the database tables").set_defaults(
        func=_cmd_init_db
    )

    company = sub.add_parser("company", help="Manage companies")
    company_sub = company.add_subparsers(dest="company_command", required=True)
    create = company_sub.add_parser("create", help="Create a company")
    create.add_argument("--name", required=True)
    create.add_argument("--owner", required=True)
    create.set_defaults(func=_cmd_company_create)
    company_sub.add_parser("list", help="List companies").set_defaults(
        func=_cmd_company_list
    )

    income = sub.add_parser("income", help="Record income")
    income_sub = income.add_subparsers(dest="income_command", required=True)
    income_add = income_sub.add_parser("add", help="Record income (admins only)")
    income_add.add_argument("--company", type=int, required=True)
    income_add.add_argument("--amount", required=True)
    income_add.add_argument("--by", required=True, help="Who is recording this")
    income_add.add_argument(
        "--role", default=Role.ADMIN.value, choices=[r.value for r in Role]
    )
    income_add.add_argument("--description")
    income_add.add_argument("--category")
    income_add.set_defaults(func=_cmd_income_add)

    expense = sub.add_parser("expense", help="Spend money or manage requests")
    expense_sub = expense.add_subparsers(dest="expense_command", required=True)

    submit = expense_sub.add_parser("submit", help="Spend, or request to spend")
    submit.add_argument("--company", type=int, required=True)
    submit.add_argument("--amount", required=True)
    submit.add_argument("--by", required=True)
    submit.add_argument(
        "--role", default=Role.REGULAR_USER.value, choices=[r.value for r in Role]
    )
    submit.add_argument("--description")
    submit.add_argument("--category")
    submit.set_defaults(func=_cmd_expense_submit)

    approve = expense_sub.add_parser("approve", help="Approve a pending expense")
    approve.add_argument("--id", type=int, required=True)
    approve.add_argument("--by", required=True)
    approve.add_argument(
        "--role", default=Role.ADMIN.value, choices=[r.value for r in Role]
    )
    approve.add_argument("--note")
    approve.set_defaults(func=_cmd_expense_approve)

    reject = expense_sub.add_parser("reject", help="Reject a pending expense")
    reject.add_argument("--id", type=int, required=True)
    reject.add_argument("--by", required=True)
    reject.add_argument(
        "--role", default=Role.ADMIN.value, choices=[r.value for r in Role]
    )
    reject.add_argument("--note")
    reject.set_defaults(func=_cmd_expense_reject)

    report = sub.add_parser("report", help="Show a company's financial summary")
    report.add_argument("--company", type=int, required=True)
    report.set_defaults(func=_cmd_report)

    ai = sub.add_parser("ai", help="Optional AI-assisted features")
    ai_sub = ai.add_subparsers(dest="ai_command", required=True)

    ai_parse = ai_sub.add_parser(
        "log", help="Log a transaction written as a plain sentence"
    )
    ai_parse.add_argument("text", help='e.g. "paid 45.50 for an uber to the airport"')
    ai_parse.add_argument("--company", type=int, required=True)
    ai_parse.add_argument("--by", required=True)
    ai_parse.add_argument(
        "--role", default=Role.REGULAR_USER.value, choices=[r.value for r in Role]
    )
    ai_parse.add_argument(
        "--dry-run", action="store_true", help="Show what was understood, record nothing"
    )
    ai_parse.set_defaults(func=_cmd_ai_parse)

    ai_summary = ai_sub.add_parser("summary", help="Plain-English financial summary")
    ai_summary.add_argument("--company", type=int, required=True)
    ai_summary.set_defaults(func=_cmd_ai_summary)

    ai_check = ai_sub.add_parser(
        "check", help="Flag duplicates and unusual expenses (no AI provider needed)"
    )
    ai_check.add_argument("--company", type=int, required=True)
    ai_check.set_defaults(func=_cmd_ai_check)

    transactions = sub.add_parser("transactions", help="List transactions")
    transactions.add_argument("--company", type=int, required=True)
    transactions.add_argument(
        "--status", choices=[s.value for s in TransactionStatus], default=None
    )
    transactions.add_argument("--limit", type=int, default=50)
    transactions.set_defaults(func=_cmd_transactions)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except AccountManagerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except AIError as exc:
        print(f"AI error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
