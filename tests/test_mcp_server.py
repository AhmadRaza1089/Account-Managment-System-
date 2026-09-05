"""Tests for the MCP server.

These call the tools the way an MCP client does, rather than only checking
that they are registered — an earlier version of this file did the latter
and so never noticed that the tools themselves were broken.
"""

import json

import pytest

from account_manager.mcp_server import mcp

EXPECTED_TOOLS = {
    "create_company",
    "list_companies",
    "get_report",
    "add_income",
    "submit_expense",
    "approve_expense",
    "reject_expense",
    "list_transactions",
    "check_for_anomalies",
    "reverse_transaction",
    "export_ledger_csv",
}


async def call(tool_name: str, /, **arguments):
    """Invoke a tool and return its parsed result.

    tool_name is positional-only so it can't collide with a tool argument
    that is itself called "name".
    """
    result = await mcp.call_tool(tool_name, arguments)
    if isinstance(result, tuple):
        result = result[1]
    if isinstance(result, dict):
        return result.get("result", result)
    # Content blocks: take the text payload.
    return json.loads(result[0].text)


async def _grant(company_id: int, username: str, role: str) -> None:
    """Membership is granted by an administrator through the CLI, not by an
    AI agent, so there is deliberately no MCP tool for it."""
    from account_manager import auth, db
    from account_manager.models import Role

    with db.session_scope() as session:
        user = auth.get_user(session, username)
        actor = auth.actor_for(session, auth.current_user(session), company_id)
        auth.add_member(session, company_id, user, Role(role), actor=actor)


# ---------------------------------------------------------------- registry


async def test_all_expected_tools_are_registered():
    names = {tool.name for tool in await mcp.list_tools()}
    assert EXPECTED_TOOLS <= names


async def test_tools_have_descriptions():
    for tool in await mcp.list_tools():
        assert tool.description, f"Tool {tool.name} is missing a description."


# ------------------------------------------------------------------ usage


async def test_full_expense_approval_flow(mcp_credential, staff_user, login_as):
    """A staff member's request waits; an admin approves it."""
    company = await call("create_company", name="Acme", owner_name="Ahmed")
    company_id = company["company_id"]
    await call("add_income", company_id=company_id, amount="1000.00")
    await _grant(company_id, "raza", "regular_user")

    login_as(staff_user)
    submitted = await call(
        "submit_expense",
        company_id=company_id,
        amount="250.00",
        description="Office chairs",
    )
    assert submitted["transaction"]["status"] == "pending"
    assert submitted["transaction"]["created_by"] == "raza"
    assert submitted["available"] == "750.00"

    login_as(mcp_credential)
    approved = await call(
        "approve_expense", transaction_id=submitted["transaction"]["id"]
    )
    assert approved["transaction"]["status"] == "approved"
    assert approved["balance"] == "750.00"

    report = await call("get_report", company_id=company_id)
    assert report["income"] == "1000.00"
    assert report["expense"] == "250.00"
    assert report["pending_count"] == 0


async def test_rejecting_releases_funds(mcp_credential, staff_user, login_as):
    company_id = (await call("create_company", name="Acme", owner_name="Ahmed"))[
        "company_id"
    ]
    await call("add_income", company_id=company_id, amount="500")
    await _grant(company_id, "raza", "regular_user")

    login_as(staff_user)
    submitted = await call("submit_expense", company_id=company_id, amount="200")
    login_as(mcp_credential)
    rejected = await call(
        "reject_expense",
        transaction_id=submitted["transaction"]["id"],
        note="not needed",
    )
    assert rejected["transaction"]["status"] == "rejected"
    assert rejected["available"] == "500.00"


async def test_overspending_is_refused(mcp_credential):
    company_id = (await call("create_company", name="Acme", owner_name="Ahmed"))[
        "company_id"
    ]
    await call("add_income", company_id=company_id, amount="100")
    with pytest.raises(Exception, match="exceeds the available balance"):
        await call(
            "submit_expense",
            company_id=company_id,
            amount="5000",
        )


async def test_a_staff_credential_cannot_record_income(
    mcp_credential, staff_user, login_as
):
    """The role comes from the credential, so an agent cannot claim admin."""
    company_id = (await call("create_company", name="Acme", owner_name="Ahmed"))[
        "company_id"
    ]
    await _grant(company_id, "raza", "regular_user")

    login_as(staff_user)
    with pytest.raises(Exception, match="cannot record income"):
        await call("add_income", company_id=company_id, amount="100")


async def test_companies_you_cannot_reach_are_invisible(
    mcp_credential, staff_user, login_as
):
    company_id = (await call("create_company", name="Acme", owner_name="Ahmed"))[
        "company_id"
    ]

    login_as(staff_user)
    assert await call("list_companies") == []
    with pytest.raises(Exception, match=f"No company with id {company_id}"):
        await call("get_report", company_id=company_id)


async def test_tools_expose_no_way_to_claim_a_role():
    """Regression: the tools used to take actor_name and role as arguments,
    so anything talking to the server could simply declare itself an admin."""
    for tool in await mcp.list_tools():
        properties = set(tool.inputSchema.get("properties") or {})
        assert not properties & {"role", "actor_name"}, tool.name


async def test_an_invalid_credential_is_refused(database, monkeypatch):
    from account_manager import auth as auth_module

    monkeypatch.setenv(auth_module.TOKEN_ENV_VAR, "not-a-real-token")
    with pytest.raises(Exception, match="not valid"):
        await call("list_companies")


async def test_anomaly_check_runs_without_any_ai_provider(mcp_credential):
    """The anomaly tool is statistical, so it must work with no API key."""
    company_id = (await call("create_company", name="Acme", owner_name="Ahmed"))[
        "company_id"
    ]
    await call("add_income", company_id=company_id, amount="1000")
    for _ in range(2):
        await call(
            "submit_expense",
            company_id=company_id,
            amount="40",
            description="Same lunch",
        )

    findings = await call("check_for_anomalies", company_id=company_id)
    assert any(f["kind"] == "possible_duplicate" for f in findings)


async def test_list_transactions_filters_by_status(
    mcp_credential, staff_user, login_as
):
    company_id = (await call("create_company", name="Acme", owner_name="Ahmed"))[
        "company_id"
    ]
    await call("add_income", company_id=company_id, amount="900")
    await _grant(company_id, "raza", "regular_user")

    login_as(staff_user)
    await call("submit_expense", company_id=company_id, amount="60")
    login_as(mcp_credential)
    pending = await call("list_transactions", company_id=company_id, status="pending")
    assert len(pending) == 1
    assert pending[0]["amount"] == "60.00"


async def test_reversing_through_mcp_restores_the_balance(mcp_credential):
    company_id = (await call("create_company", name="Acme", owner_name="Ahmed"))[
        "company_id"
    ]
    await call("add_income", company_id=company_id, amount="1000")
    spent = await call(
        "submit_expense",
        company_id=company_id,
        amount="400",
    )
    assert spent["balance"] == "600.00"

    reversed_txn = await call(
        "reverse_transaction",
        transaction_id=spent["transaction"]["id"],
        reason="charged to the wrong company",
    )
    assert reversed_txn["transaction"]["status"] == "reversed"
    assert reversed_txn["balance"] == "1000.00"


async def test_csv_export_through_mcp(mcp_credential):
    company_id = (await call("create_company", name="Acme", owner_name="Ahmed",
                             currency="EUR"))["company_id"]
    await call("add_income", company_id=company_id, amount="120")

    data = await call("export_ledger_csv", company_id=company_id)
    assert "id,occurred_on,type,status,amount,currency" in data
    assert "120.00" in data
    assert "EUR" in data


async def test_a_bad_date_is_reported_clearly(mcp_credential):
    company_id = (await call("create_company", name="Acme", owner_name="Ahmed"))[
        "company_id"
    ]
    with pytest.raises(Exception, match="YYYY-MM-DD"):
        await call("get_report", company_id=company_id, since="15-01-2026")
