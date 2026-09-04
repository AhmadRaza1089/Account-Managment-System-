"""Command line interface.

    account-manager init-db
    account-manager user create --username ahmed --superuser
    account-manager login --username ahmed
    account-manager company create --name Acme --owner Ahmed
    account-manager income add --company 1 --amount 10000
    account-manager expense submit --company 1 --amount 500
    account-manager expense approve --id 2
    account-manager report --company 1

Who you are comes from the login, not from a flag, so nobody can claim a
role they have not been granted.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from datetime import date

from . import __version__, auth, services
from .ai.base import AIError
from .db import create_all, init_engine, session_scope
from .errors import AccountManagerError
from .models import Role, TransactionStatus
from .security import WeakPassword

PASSWORD_ENV_VAR = "ACCOUNT_MANAGER_PASSWORD"


def _iso_date(value: str) -> date:
    """A YYYY-MM-DD argument."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a date in YYYY-MM-DD form."
        ) from None


def _read_password(prompt: str = "Password: ", *, confirm: bool = False) -> str:
    """Take a password from the environment if set, otherwise prompt.

    The environment variable exists so scripts and container start-up can
    create the first user without a terminal.
    """
    from_env = os.environ.get(PASSWORD_ENV_VAR)
    if from_env:
        return from_env

    password = getpass.getpass(prompt)
    if confirm and password != getpass.getpass("Repeat password: "):
        raise AccountManagerError("The passwords did not match.")
    return password


def _print_transaction(txn_dict: dict) -> None:
    print(
        f"#{txn_dict['id']:<5} {txn_dict.get('occurred_on') or '':<10} "
        f"{txn_dict['type']:<8} {txn_dict['amount']:>12} "
        f"{txn_dict['status']:<9} by {txn_dict['created_by']}"
        + (f" — {txn_dict['description']}" if txn_dict.get("description") else "")
    )


# --------------------------------------------------------------------------
# Setup and accounts
# --------------------------------------------------------------------------


def _cmd_init_db(_args: argparse.Namespace) -> int:
    init_engine()
    create_all()
    with session_scope() as session:
        if auth.count_users(session) == 0:
            print(
                "Database ready. Create the first account with:\n"
                "  account-manager user create --username YOU --superuser"
            )
            return 0
    print("Database ready.")
    return 0


def _cmd_user_create(args: argparse.Namespace) -> int:
    with session_scope() as session:
        first_run = auth.count_users(session) == 0
        if not first_run:
            # Only a superuser may add accounts; the very first one is the
            # exception, since there is nobody to authorise it yet.
            actor_user = auth.current_user(session)
            if not actor_user.is_superuser:
                raise AccountManagerError("Only a superuser can create accounts.")

        password = _read_password(confirm=True)
        user = auth.create_user(
            session,
            args.username,
            password,
            is_superuser=args.superuser or first_run,
        )
        if first_run and not args.superuser:
            print("(the first account is always a superuser)")
        print(f"Created user {user.username!r}.")
        print("Log in with: account-manager login --username " + user.username)
    return 0


def _cmd_user_list(_args: argparse.Namespace) -> int:
    with session_scope() as session:
        auth.current_user(session)
        for user in auth.list_users(session):
            flags = []
            if user.is_superuser:
                flags.append("superuser")
            if not user.is_active:
                flags.append("disabled")
            suffix = f"  [{', '.join(flags)}]" if flags else ""
            print(f"#{user.id:<4} {user.username}{suffix}")
    return 0


def _cmd_user_passwd(args: argparse.Namespace) -> int:
    with session_scope() as session:
        actor_user = auth.current_user(session)
        target = auth.get_user(session, args.username or actor_user.username)
        if target.id != actor_user.id and not actor_user.is_superuser:
            raise AccountManagerError("Only a superuser can change another password.")

        auth.set_password(session, target, _read_password("New password: ", confirm=True))
        print(f"Password changed for {target.username!r}. Existing logins were ended.")
    return 0


def _cmd_user_disable(args: argparse.Namespace) -> int:
    with session_scope() as session:
        actor_user = auth.current_user(session)
        if not actor_user.is_superuser:
            raise AccountManagerError("Only a superuser can disable accounts.")
        target = auth.get_user(session, args.username)
        if target.id == actor_user.id:
            raise AccountManagerError("You cannot disable your own account.")
        auth.set_active(session, target, active=not args.enable)
        print(f"{target.username!r} is now {'active' if args.enable else 'disabled'}.")
    return 0


def _cmd_login(args: argparse.Namespace) -> int:
    with session_scope() as session:
        user = auth.authenticate(session, args.username, _read_password())
        token = auth.issue_token(session, user, name=args.name)
        username = user.username
    path = auth.save_credentials(username, token)
    print(f"Logged in as {username}. Credentials saved to {path}.")
    return 0


def _cmd_logout(_args: argparse.Namespace) -> int:
    token = auth.current_token()
    if token:
        with session_scope() as session:
            auth.revoke_token(session, token)
    auth.clear_credentials()
    print("Logged out.")
    return 0


def _cmd_whoami(_args: argparse.Namespace) -> int:
    with session_scope() as session:
        user = auth.current_user(session)
        companies = auth.visible_companies(session, user)
        role = "superuser" if user.is_superuser else "user"
        print(f"{user.username} ({role})")
        if companies:
            print("Companies:")
            for company in companies:
                actor = auth.actor_for(session, user, company.id)
                print(f"  #{company.id:<4} {company.name} — {actor.role.value}")
        else:
            print("No companies yet.")
    return 0


def _cmd_token_create(args: argparse.Namespace) -> int:
    """A long-lived credential, for the MCP server or a script."""
    with session_scope() as session:
        user = auth.current_user(session)
        token = auth.issue_token(session, user, name=args.name)
    print(token)
    print(
        f"\nThis is shown once. Give it to the MCP server as "
        f"{auth.TOKEN_ENV_VAR}.",
        file=sys.stderr,
    )
    return 0


# --------------------------------------------------------------------------
# Companies and membership
# --------------------------------------------------------------------------


def _cmd_company_create(args: argparse.Namespace) -> int:
    with session_scope() as session:
        user = auth.current_user(session)
        company = services.create_company(
            session, args.name, args.owner, currency=args.currency, creator=user
        )
        print(
            f"Created company #{company.id}: {company.name} "
            f"(owner {company.owner_name}, {company.currency})"
        )
    return 0


def _cmd_company_list(_args: argparse.Namespace) -> int:
    with session_scope() as session:
        user = auth.current_user(session)
        companies = auth.visible_companies(session, user)
        if not companies:
            print("No companies yet. Create one with: company create --name X --owner Y")
        for company in companies:
            print(
                f"#{company.id:<5} {company.name}  "
                f"(owner {company.owner_name}, {company.currency})"
            )
    return 0


def _cmd_member_add(args: argparse.Namespace) -> int:
    with session_scope() as session:
        actor = auth.actor_for(session, auth.current_user(session), args.company)
        target = auth.get_user(session, args.username)
        auth.add_member(session, args.company, target, Role(args.role), actor=actor)
        print(f"{target.username!r} is now {args.role} of company {args.company}.")
    return 0


def _cmd_member_remove(args: argparse.Namespace) -> int:
    with session_scope() as session:
        actor = auth.actor_for(session, auth.current_user(session), args.company)
        target = auth.get_user(session, args.username)
        if auth.remove_member(session, args.company, target, actor=actor):
            print(f"Removed {target.username!r} from company {args.company}.")
        else:
            print(f"{target.username!r} was not a member of company {args.company}.")
    return 0


def _cmd_member_list(args: argparse.Namespace) -> int:
    with session_scope() as session:
        auth.actor_for(session, auth.current_user(session), args.company)
        members = auth.list_members(session, args.company)
        if not members:
            print("No members yet (superusers reach every company without one).")
        for member in members:
            print(f"{member.user.username:<20} {member.role.value}")
    return 0


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------


def _cmd_income_add(args: argparse.Namespace) -> int:
    with session_scope() as session:
        actor = auth.actor_for(session, auth.current_user(session), args.company)
        txn = services.add_income(
            session,
            args.company,
            actor,
            args.amount,
            description=args.description,
            category=args.category,
            occurred_on=args.date,
        )
        balances = services.get_balances(session, args.company)
        _print_transaction(txn.as_dict())
        print(f"Balance: {balances.balance}  Available: {balances.available}")
    return 0


def _cmd_expense_submit(args: argparse.Namespace) -> int:
    with session_scope() as session:
        actor = auth.actor_for(session, auth.current_user(session), args.company)
        txn = services.submit_expense(
            session,
            args.company,
            actor,
            args.amount,
            description=args.description,
            category=args.category,
            occurred_on=args.date,
        )
        balances = services.get_balances(session, args.company)
        _print_transaction(txn.as_dict())
        print(f"Balance: {balances.balance}  Available: {balances.available}")
    return 0


def _decide(args: argparse.Namespace, decide) -> int:
    with session_scope() as session:
        user = auth.current_user(session)
        company_id = services.company_id_for_transaction(session, args.id)
        actor = auth.actor_for(session, user, company_id)
        txn = decide(session, args.id, actor, note=args.note)
        _print_transaction(txn.as_dict())
    return 0


def _cmd_expense_approve(args: argparse.Namespace) -> int:
    return _decide(args, services.approve_expense)


def _cmd_expense_reject(args: argparse.Namespace) -> int:
    return _decide(args, services.reject_expense)


def _cmd_expense_reverse(args: argparse.Namespace) -> int:
    with session_scope() as session:
        user = auth.current_user(session)
        company_id = services.company_id_for_transaction(session, args.id)
        actor = auth.actor_for(session, user, company_id)
        txn = services.reverse_transaction(session, args.id, actor, reason=args.reason)
        balances = services.get_balances(session, company_id)
        _print_transaction(txn.as_dict())
        print(f"Balance: {balances.balance}  Available: {balances.available}")
        if balances.balance < 0:
            print(
                "Warning: the balance is now negative — money was spent against "
                "an entry that has since been reversed. Further spending is "
                "blocked until this is corrected."
            )
    return 0


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def _cmd_report(args: argparse.Namespace) -> int:
    with session_scope() as session:
        auth.actor_for(session, auth.current_user(session), args.company)
        report = services.get_report(
            session, args.company, since=args.since, until=args.until
        )
    print(f"{report['company']} (owner {report['owner']}, {report['currency']})")
    print(f"  Income:            {report['income']:>14}")
    print(f"  Expenses:          {report['expense']:>14}")
    print(f"  Awaiting approval: {report['pending_expense']:>14}")
    print(f"  Balance:           {report['balance']:>14}")
    print(f"  Available:         {report['available']:>14}")

    if "period" in report:
        period = report["period"]
        label = f"{period['since'] or 'start'} to {period['until'] or 'today'}"
        print(f"\nPeriod {label}:")
        print(f"  Income:            {period['income']:>14}")
        print(f"  Expenses:          {period['expense']:>14}")
        print(f"  Net:               {period['net']:>14}")

    if report["pending_transactions"]:
        print("\nAwaiting approval:")
        for txn in report["pending_transactions"]:
            _print_transaction(txn)
    return 0


def _cmd_transactions(args: argparse.Namespace) -> int:
    status = TransactionStatus(args.status) if args.status else None
    with session_scope() as session:
        auth.actor_for(session, auth.current_user(session), args.company)
        transactions = services.list_transactions(
            session,
            args.company,
            status=status,
            since=args.since,
            until=args.until,
            limit=args.limit,
            offset=args.offset,
        )
        if not transactions:
            print("No transactions found.")
        for txn in transactions:
            _print_transaction(txn.as_dict())
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    with session_scope() as session:
        auth.actor_for(session, auth.current_user(session), args.company)
        data = services.export_csv(
            session, args.company, since=args.since, until=args.until
        )
    if args.output:
        with open(args.output, "w", newline="", encoding="utf-8") as handle:
            handle.write(data)
        print(f"Wrote {args.output}")
    else:
        print(data, end="")
    return 0


# --------------------------------------------------------------------------
# AI
# --------------------------------------------------------------------------


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

    with session_scope() as session:
        actor = auth.actor_for(session, auth.current_user(session), args.company)
        record = services.add_income if parsed.type == "income" else services.submit_expense
        txn = record(
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
        auth.actor_for(session, auth.current_user(session), args.company)
        print(summarise(session, args.company, get_provider()))
    return 0


def _cmd_ai_check(args: argparse.Namespace) -> int:
    """Statistical checks — works with no AI provider configured."""
    from .ai.anomalies import detect_anomalies

    with session_scope() as session:
        auth.actor_for(session, auth.current_user(session), args.company)
        findings = detect_anomalies(session, args.company)

    if not findings:
        print("No anomalies found.")
        return 0
    for finding in findings:
        print(f"[{finding.severity:<6}] {finding.message}")
    return 0


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="account-manager", description="Track company income, expenses, approvals."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create the database tables").set_defaults(
        func=_cmd_init_db
    )

    # -- accounts ----------------------------------------------------------
    user = sub.add_parser("user", help="Manage accounts")
    user_sub = user.add_subparsers(dest="user_command", required=True)

    user_create = user_sub.add_parser("create", help="Create an account")
    user_create.add_argument("--username", required=True)
    user_create.add_argument(
        "--superuser", action="store_true", help="Reaches every company"
    )
    user_create.set_defaults(func=_cmd_user_create)

    user_sub.add_parser("list", help="List accounts").set_defaults(func=_cmd_user_list)

    passwd = user_sub.add_parser("passwd", help="Change a password")
    passwd.add_argument("--username", help="Defaults to your own account")
    passwd.set_defaults(func=_cmd_user_passwd)

    disable = user_sub.add_parser("disable", help="Disable an account")
    disable.add_argument("--username", required=True)
    disable.set_defaults(func=_cmd_user_disable, enable=False)

    enable = user_sub.add_parser("enable", help="Re-enable an account")
    enable.add_argument("--username", required=True)
    enable.set_defaults(func=_cmd_user_disable, enable=True)

    login = sub.add_parser("login", help="Log in and save credentials")
    login.add_argument("--username", required=True)
    login.add_argument("--name", default="cli", help="A label for this login")
    login.set_defaults(func=_cmd_login)

    sub.add_parser("logout", help="Log out and forget credentials").set_defaults(
        func=_cmd_logout
    )
    sub.add_parser("whoami", help="Show who you are logged in as").set_defaults(
        func=_cmd_whoami
    )

    token = sub.add_parser("token", help="Long-lived credentials")
    token_sub = token.add_subparsers(dest="token_command", required=True)
    token_create = token_sub.add_parser("create", help="Create one, e.g. for MCP")
    token_create.add_argument("--name", default="mcp")
    token_create.set_defaults(func=_cmd_token_create)

    # -- companies ---------------------------------------------------------
    company = sub.add_parser("company", help="Manage companies")
    company_sub = company.add_subparsers(dest="company_command", required=True)
    create = company_sub.add_parser("create", help="Create a company")
    create.add_argument("--name", required=True)
    create.add_argument("--owner", required=True)
    create.add_argument(
        "--currency", default="USD", help="Three-letter code, e.g. USD, EUR, PKR"
    )
    create.set_defaults(func=_cmd_company_create)
    company_sub.add_parser("list", help="List companies").set_defaults(
        func=_cmd_company_list
    )

    member = sub.add_parser("member", help="Who can use a company")
    member_sub = member.add_subparsers(dest="member_command", required=True)
    member_add = member_sub.add_parser("add", help="Grant access")
    member_add.add_argument("--company", type=int, required=True)
    member_add.add_argument("--username", required=True)
    member_add.add_argument(
        "--role", default=Role.REGULAR_USER.value, choices=[r.value for r in Role]
    )
    member_add.set_defaults(func=_cmd_member_add)

    member_remove = member_sub.add_parser("remove", help="Revoke access")
    member_remove.add_argument("--company", type=int, required=True)
    member_remove.add_argument("--username", required=True)
    member_remove.set_defaults(func=_cmd_member_remove)

    member_list = member_sub.add_parser("list", help="List members")
    member_list.add_argument("--company", type=int, required=True)
    member_list.set_defaults(func=_cmd_member_list)

    # -- money -------------------------------------------------------------
    income = sub.add_parser("income", help="Record income")
    income_sub = income.add_subparsers(dest="income_command", required=True)
    income_add = income_sub.add_parser("add", help="Record income (admins only)")
    income_add.add_argument("--company", type=int, required=True)
    income_add.add_argument("--amount", required=True)
    income_add.add_argument("--description")
    income_add.add_argument("--category")
    income_add.add_argument(
        "--date", type=_iso_date, help="When the money moved (YYYY-MM-DD, default today)"
    )
    income_add.set_defaults(func=_cmd_income_add)

    expense = sub.add_parser("expense", help="Spend money or manage requests")
    expense_sub = expense.add_subparsers(dest="expense_command", required=True)

    submit = expense_sub.add_parser("submit", help="Spend, or request to spend")
    submit.add_argument("--company", type=int, required=True)
    submit.add_argument("--amount", required=True)
    submit.add_argument("--description")
    submit.add_argument("--category")
    submit.add_argument(
        "--date", type=_iso_date, help="When the money moved (YYYY-MM-DD, default today)"
    )
    submit.set_defaults(func=_cmd_expense_submit)

    approve = expense_sub.add_parser("approve", help="Approve a pending expense")
    approve.add_argument("--id", type=int, required=True)
    approve.add_argument("--note")
    approve.set_defaults(func=_cmd_expense_approve)

    reject = expense_sub.add_parser("reject", help="Reject a pending expense")
    reject.add_argument("--id", type=int, required=True)
    reject.add_argument("--note")
    reject.set_defaults(func=_cmd_expense_reject)

    reverse = expense_sub.add_parser(
        "reverse", help="Undo an approved transaction entered by mistake"
    )
    reverse.add_argument("--id", type=int, required=True)
    reverse.add_argument("--reason", required=True, help="Kept on the record")
    reverse.set_defaults(func=_cmd_expense_reverse)

    # -- reading -----------------------------------------------------------
    report = sub.add_parser("report", help="Show a company's financial summary")
    report.add_argument("--company", type=int, required=True)
    report.add_argument("--since", type=_iso_date, help="Period start (YYYY-MM-DD)")
    report.add_argument("--until", type=_iso_date, help="Period end (YYYY-MM-DD)")
    report.set_defaults(func=_cmd_report)

    export = sub.add_parser("export", help="Export the ledger as CSV")
    export.add_argument("--company", type=int, required=True)
    export.add_argument("--since", type=_iso_date)
    export.add_argument("--until", type=_iso_date)
    export.add_argument("--output", help="File to write (default: stdout)")
    export.set_defaults(func=_cmd_export)

    transactions = sub.add_parser("transactions", help="List transactions")
    transactions.add_argument("--company", type=int, required=True)
    transactions.add_argument(
        "--status", choices=[s.value for s in TransactionStatus], default=None
    )
    transactions.add_argument("--limit", type=int, default=50)
    transactions.add_argument("--offset", type=int, default=0)
    transactions.add_argument("--since", type=_iso_date)
    transactions.add_argument("--until", type=_iso_date)
    transactions.set_defaults(func=_cmd_transactions)

    # -- ai ----------------------------------------------------------------
    ai = sub.add_parser("ai", help="Optional AI-assisted features")
    ai_sub = ai.add_subparsers(dest="ai_command", required=True)

    ai_parse = ai_sub.add_parser(
        "log", help="Log a transaction written as a plain sentence"
    )
    ai_parse.add_argument("text", help='e.g. "paid 45.50 for an uber to the airport"')
    ai_parse.add_argument("--company", type=int, required=True)
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

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except WeakPassword as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except AccountManagerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except AIError as exc:
        print(f"AI error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
