"""MCP server exposing this accounting system to AI agents
(Claude Desktop, Claude Code, or any other MCP-compatible client).

Run with:
    python -m account_manager.mcp_server

SECURITY: there is no authentication. Callers state their own name and
role, so roles describe intent, not identity — anyone who can reach this
server can act as an admin. Run it locally against your own database, and
don't expose it to a network you don't control.
"""

import logging
from datetime import date

from mcp.server.fastmcp import FastMCP

from . import services
from .db import session_scope
from .models import Actor, Role, TransactionStatus

logger = logging.getLogger(__name__)

mcp = FastMCP("account-manager")


def _parse_date(value: str | None) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{value!r} is not a date in YYYY-MM-DD form.") from None


def _actor(name: str, role: str) -> Actor:
    try:
        return Actor(name=name, role=Role(role))
    except ValueError:
        valid = ", ".join(r.value for r in Role)
        raise ValueError(f"Unknown role '{role}'. Valid roles: {valid}.") from None


@mcp.tool()
def create_company(name: str, owner_name: str, currency: str = "USD") -> dict:
    """Create a new company to track income and expenses for.

    currency is a three-letter code such as USD or PKR, used for display.
    """
    with session_scope() as session:
        company = services.create_company(session, name, owner_name, currency=currency)
        return {
            "company_id": company.id,
            "name": company.name,
            "owner_name": company.owner_name,
            "currency": company.currency,
        }


@mcp.tool()
def list_companies() -> list[dict]:
    """List every company in this system."""
    with session_scope() as session:
        return [
            {"company_id": c.id, "name": c.name, "owner_name": c.owner_name}
            for c in services.list_companies(session)
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
        return services.get_report(
            session, company_id, since=_parse_date(since), until=_parse_date(until)
        )


@mcp.tool()
def add_income(
    company_id: int,
    actor_name: str,
    amount: str,
    role: str = Role.ADMIN.value,
    description: str | None = None,
    category: str | None = None,
) -> dict:
    """Record income for a company. Only admins may record income.

    amount is a decimal string such as "1500.00".
    """
    with session_scope() as session:
        txn = services.add_income(
            session,
            company_id,
            _actor(actor_name, role),
            amount,
            description=description,
            category=category,
        )
        balances = services.get_balances(session, company_id)
        return {"transaction": txn.as_dict(), **balances.as_dict()}


@mcp.tool()
def submit_expense(
    company_id: int,
    actor_name: str,
    role: str,
    amount: str,
    description: str | None = None,
    category: str | None = None,
) -> dict:
    """Spend money, or request to spend it.

    role is "admin", "owner", or "regular_user". An admin's expense is
    approved immediately; a regular_user's waits for approval. Either way
    it is refused if it exceeds the available balance.

    amount is a decimal string such as "250.00".
    """
    with session_scope() as session:
        txn = services.submit_expense(
            session,
            company_id,
            _actor(actor_name, role),
            amount,
            description=description,
            category=category,
        )
        balances = services.get_balances(session, company_id)
        return {"transaction": txn.as_dict(), **balances.as_dict()}


@mcp.tool()
def approve_expense(
    transaction_id: int,
    actor_name: str,
    role: str = Role.ADMIN.value,
    note: str | None = None,
) -> dict:
    """Approve an expense that is awaiting approval. Admins only."""
    with session_scope() as session:
        txn = services.approve_expense(
            session, transaction_id, _actor(actor_name, role), note=note
        )
        balances = services.get_balances(session, txn.company_id)
        return {"transaction": txn.as_dict(), **balances.as_dict()}


@mcp.tool()
def reject_expense(
    transaction_id: int,
    actor_name: str,
    role: str = Role.ADMIN.value,
    note: str | None = None,
) -> dict:
    """Reject an expense awaiting approval, releasing the funds it held. Admins only."""
    with session_scope() as session:
        txn = services.reject_expense(
            session, transaction_id, _actor(actor_name, role), note=note
        )
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
def reverse_transaction(
    transaction_id: int,
    actor_name: str,
    reason: str,
    role: str = Role.ADMIN.value,
) -> dict:
    """Undo an approved transaction that was entered wrongly. Admins only.

    The original is kept and marked reversed rather than deleted, so the
    correction is visible in the ledger. Use reject instead for something
    still awaiting approval. Reversing income can leave the balance
    negative, which blocks further spending until it is corrected.
    """
    with session_scope() as session:
        txn = services.reverse_transaction(
            session, transaction_id, _actor(actor_name, role), reason=reason
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
        return [finding.as_dict() for finding in detect_anomalies(session, company_id)]


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    from .db import create_all, init_engine

    init_engine()
    create_all()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
