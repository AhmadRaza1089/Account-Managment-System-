"""MCP server exposing this accounting system as tools for AI agents
(Claude Desktop, Claude Code, or any other MCP-compatible client).

Run with:
    python -m account_manager.mcp_server

Requires the same environment variables as the rest of the app (see
.env.example) — the server connects to your own database, same as
python -m account_manager.main.

Note: role/approval checks here mirror the business rules in models.py,
but there is no authentication layer yet — the caller states who they
are (name/role) as tool arguments. Treat this as a trusted, single-tenant
tool for now; real per-user auth is on the roadmap (see README).
"""

import logging
from decimal import Decimal, InvalidOperation

from mcp.server.fastmcp import FastMCP

from . import db, repository
from .config import DatabaseSettings
from .models import Account, Company, ExpenseManager, Role, User

logger = logging.getLogger(__name__)

mcp = FastMCP("account-manager")


def _parse_amount(amount: str) -> Decimal:
    try:
        value = Decimal(amount)
    except InvalidOperation as exc:
        raise ValueError(f"'{amount}' is not a valid decimal amount.") from exc
    if value <= 0:
        raise ValueError("Amount must be positive.")
    return value


def _load_company_and_account(company_id: int) -> tuple[Company, Account]:
    company = repository.get_company(company_id)
    if company is None:
        raise ValueError(f"No company with id {company_id}.")
    account = repository.get_account_by_company(company_id)
    if account is None:
        raise ValueError(f"Company {company_id} has no linked account.")
    return company, account


@mcp.tool()
def create_company(name: str, owner_name: str) -> dict:
    """Create a new company with its own income/expense account.

    Returns the new company_id and account_id.
    """
    company = Company(name=name, owner_name=owner_name)
    company_id = repository.insert_company(company)
    account = Account(company_id=company_id)
    account_id = repository.insert_account(account)
    return {"company_id": company_id, "account_id": account_id}


@mcp.tool()
def list_companies() -> list[dict]:
    """List every company known to this system."""
    return [
        {"company_id": c.id, "name": c.name, "owner_name": c.owner_name}
        for c in repository.list_companies()
    ]


@mcp.tool()
def get_report(company_id: int) -> dict:
    """Get a company's current income, expense, pending expense, and balance."""
    company, account = _load_company_and_account(company_id)
    total_expenses, balance = ExpenseManager.calculate_budget(
        account.income, [account.expense]
    )
    return {
        "company": company.name,
        "owner": company.owner_name,
        "income": str(account.income),
        "expense": str(total_expenses),
        "pending_expense": str(account.pending_expense),
        "balance": str(balance),
    }


@mcp.tool()
def add_income(company_id: int, admin_name: str, amount: str) -> dict:
    """Record income for a company. Only an admin may add income.

    amount is a decimal string, e.g. "1500.00".
    """
    _, account = _load_company_and_account(company_id)
    admin = User(name=admin_name, role=Role.ADMIN)
    account.add_income(admin, _parse_amount(amount))
    repository.save_account(account)
    return {"income": str(account.income)}


@mcp.tool()
def submit_expense(
    company_id: int,
    user_name: str,
    role: str,
    amount: str,
    approved: bool = True,
) -> dict:
    """Submit an expense against a company's account.

    role must be "admin" or "regular_user". For an admin, `approved`
    controls whether the expense is applied immediately (True) or held as
    pending (False). A regular_user's expense always goes to pending
    (or is rejected if it exceeds current income) regardless of `approved`.
    amount is a decimal string, e.g. "250.00".
    """
    _, account = _load_company_and_account(company_id)
    user = User(name=user_name, role=role, approved=approved)
    status = account.add_expense(user, _parse_amount(amount))
    repository.save_account(account)
    return {
        "status": status,
        "expense": str(account.expense),
        "pending_expense": str(account.pending_expense),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = DatabaseSettings.from_env()
    db.init_pool(settings)
    db.init_schema()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
