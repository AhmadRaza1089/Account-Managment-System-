"""MCP server exposing this accounting system to AI agents
(Claude Desktop, Claude Code, or any other MCP-compatible client).

Run with:
    python -m account_manager.mcp_server

Authentication is required. Create a credential with

    account-manager token create --name mcp

and give it to the server as ACCOUNT_MANAGER_TOKEN. Everything the server
does happens as that user, with exactly the companies and permissions that
account has — an agent cannot grant itself admin by asking.
"""

import logging
from datetime import date

from mcp.server.fastmcp import FastMCP

from . import auth, services
from .db import session_scope
from .models import TransactionStatus

logger = logging.getLogger(__name__)

mcp = FastMCP("account-manager")


def _parse_date(value: str | None) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{value!r} is not a date in YYYY-MM-DD form.") from None


def _actor(session, company_id: int):
    """The authority the configured credential has in this company."""
    return auth.actor_for(session, auth.current_user(session), company_id)


def _actor_for_transaction(session, transaction_id: int):
    company_id = services.company_id_for_transaction(session, transaction_id)
    return company_id, _actor(session, company_id)


@mcp.tool()
def create_company(name: str, owner_name: str, currency: str = "USD") -> dict:
    """Create a new company to track income and expenses for.

    currency is a three-letter code such as USD or PKR, used for display.
    """
    with session_scope() as session:
        company = services.create_company(
            session,
            name,
            owner_name,
            currency=currency,
            creator=auth.current_user(session),
        )
        return {
            "company_id": company.id,
            "name": company.name,
            "owner_name": company.owner_name,
            "currency": company.currency,
        }


@mcp.tool()
def list_companies() -> list[dict]:
    """List the companies this credential can reach."""
    with session_scope() as session:
        return [
            {
                "company_id": c.id,
                "name": c.name,
                "owner_name": c.owner_name,
                "currency": c.currency,
            }
            for c in auth.visible_companies(session, auth.current_user(session))
        ]


@mcp.tool()
def get_report(
    company_id: int, since: str | None = None, until: str | None = None
) -> dict:
    """Get a company's income, expenses, balance, and anything awaiting approval.

    'balance' is money held (income minus approved expenses). 'available'
    is what's left after subtracting expenses still awaiting approval.

    Giving since/until (YYYY-MM-DD) adds a 'period' section covering just
    those dates, while the balance still reflects everything.
    """
    with session_scope() as session:
        _actor(session, company_id)  # membership check
        return services.get_report(
            session, company_id, since=_parse_date(since), until=_parse_date(until)
        )


@mcp.tool()
def add_income(
    company_id: int,
    amount: str,
    description: str | None = None,
    category: str | None = None,
) -> dict:
    """Record income for a company. Only admins may record income.

    amount is a decimal string such as "1500.00". Who this is recorded as
    comes from the configured credential.
    """
    with session_scope() as session:
        txn = services.add_income(
            session,
            company_id,
            _actor(session, company_id),
            amount,
            description=description,
            category=category,
        )
        balances = services.get_balances(session, company_id)
        return {"transaction": txn.as_dict(), **balances.as_dict()}


@mcp.tool()
def submit_expense(
    company_id: int,
    amount: str,
    description: str | None = None,
    category: str | None = None,
) -> dict:
    """Spend money, or request to spend it.

    If the credential is an admin of this company the expense applies
    immediately; otherwise it waits for approval. Either way it is refused
    if it exceeds the available balance.

    amount is a decimal string such as "250.00".
    """
    with session_scope() as session:
        txn = services.submit_expense(
            session,
            company_id,
            _actor(session, company_id),
            amount,
            description=description,
            category=category,
        )
        balances = services.get_balances(session, company_id)
        return {"transaction": txn.as_dict(), **balances.as_dict()}


@mcp.tool()
def approve_expense(transaction_id: int, note: str | None = None) -> dict:
    """Approve an expense that is awaiting approval. Admins only."""
    with session_scope() as session:
        _, actor = _actor_for_transaction(session, transaction_id)
        txn = services.approve_expense(session, transaction_id, actor, note=note)
        balances = services.get_balances(session, txn.company_id)
        return {"transaction": txn.as_dict(), **balances.as_dict()}


@mcp.tool()
def reject_expense(transaction_id: int, note: str | None = None) -> dict:
    """Reject an expense awaiting approval, releasing the funds it held. Admins only."""
    with session_scope() as session:
        _, actor = _actor_for_transaction(session, transaction_id)
        txn = services.reject_expense(session, transaction_id, actor, note=note)
        balances = services.get_balances(session, txn.company_id)
        return {"transaction": txn.as_dict(), **balances.as_dict()}


@mcp.tool()
def list_transactions(
    company_id: int,
    status: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List a company's transactions, newest first.

    status filters to "pending", "approved", "rejected" or "reversed".
    since and until are YYYY-MM-DD dates filtering on when the money moved.
    offset pages through longer histories.
    """
    parsed_status = None
    if status is not None:
        try:
            parsed_status = TransactionStatus(status)
        except ValueError:
            valid = ", ".join(s.value for s in TransactionStatus)
            raise ValueError(
                f"Unknown status '{status}'. Valid values: {valid}."
            ) from None

    with session_scope() as session:
        _actor(session, company_id)  # membership check
        return [
            t.as_dict()
            for t in services.list_transactions(
                session,
                company_id,
                status=parsed_status,
                since=_parse_date(since),
                until=_parse_date(until),
                limit=limit,
                offset=offset,
            )
        ]


@mcp.tool()
def reverse_transaction(transaction_id: int, reason: str) -> dict:
    """Undo an approved transaction that was entered wrongly. Admins only.

    The original is kept and marked reversed rather than deleted, so the
    correction is visible in the ledger. Use reject instead for something
    still awaiting approval. Reversing income can leave the balance
    negative, which blocks further spending until it is corrected.
    """
    with session_scope() as session:
        _, actor = _actor_for_transaction(session, transaction_id)
        txn = services.reverse_transaction(
            session, transaction_id, actor, reason=reason
        )
        balances = services.get_balances(session, txn.company_id)
        return {"transaction": txn.as_dict(), **balances.as_dict()}


@mcp.tool()
def export_ledger_csv(
    company_id: int, since: str | None = None, until: str | None = None
) -> str:
    """The company's ledger as CSV, oldest first.

    since and until are YYYY-MM-DD dates filtering on when the money moved.
    """
    with session_scope() as session:
        _actor(session, company_id)  # membership check
        return services.export_csv(
            session, company_id, since=_parse_date(since), until=_parse_date(until)
        )


@mcp.tool()
def check_for_anomalies(company_id: int) -> list[dict]:
    """Flag suspicious expenses: probable duplicates, unusually large
    amounts for their category, and requests left waiting for approval.

    These are statistical checks over the ledger, not opinions — use them
    as leads to investigate, and read the underlying transactions before
    drawing conclusions about anyone.
    """
    from .ai.anomalies import detect_anomalies

    with session_scope() as session:
        _actor(session, company_id)  # membership check
        return [finding.as_dict() for finding in detect_anomalies(session, company_id)]


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    from .db import init_engine
    from .migrate import schema_is_ready

    init_engine()
    if not schema_is_ready():
        # Creating the tables here would quietly serve an empty ledger to an
        # install whose data simply hasn't been migrated yet.
        raise SystemExit(
            "The database has no schema yet. Run 'account-manager init-db' "
            "against the same DATABASE_URL first."
        )
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
