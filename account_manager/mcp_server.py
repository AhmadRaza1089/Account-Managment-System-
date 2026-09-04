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

from mcp.server.fastmcp import FastMCP

from . import services
from .db import session_scope
from .models import Actor, Role, TransactionStatus

logger = logging.getLogger(__name__)

mcp = FastMCP("account-manager")


def _actor(name: str, role: str) -> Actor:
    try:
        return Actor(name=name, role=Role(role))
    except ValueError:
        valid = ", ".join(r.value for r in Role)
        raise ValueError(f"Unknown role '{role}'. Valid roles: {valid}.") from None


@mcp.tool()
def create_company(name: str, owner_name: str) -> dict:
    """Create a new company to track income and expenses for."""
    with session_scope() as session:
        company = services.create_company(session, name, owner_name)
        return {
            "company_id": company.id,
            "name": company.name,
            "owner_name": company.owner_name,
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
def get_report(company_id: int) -> dict:
    """Get a company's income, expenses, balance, and anything awaiting approval.

    'balance' is money held (income minus approved expenses). 'available'
    is what's left after subtracting expenses still awaiting approval.
    """
    with session_scope() as session:
        return services.get_report(session, company_id)


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
    company_id: int, status: str | None = None, limit: int = 50
) -> list[dict]:
    """List a company's transactions, newest first.

    status filters to "pending", "approved", or "rejected".
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
                session, company_id, status=parsed_status, limit=limit
            )
        ]


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    from .db import create_all, init_engine

    init_engine()
    create_all()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
