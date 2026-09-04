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


# ---------------------------------------------------------------- registry


async def test_all_expected_tools_are_registered():
    names = {tool.name for tool in await mcp.list_tools()}
    assert EXPECTED_TOOLS <= names


async def test_tools_have_descriptions():
    for tool in await mcp.list_tools():
        assert tool.description, f"Tool {tool.name} is missing a description."


# ------------------------------------------------------------------ usage


async def test_full_expense_approval_flow(database):
    company = await call("create_company", name="Acme", owner_name="Ahmed")
    company_id = company["company_id"]

    await call("add_income", company_id=company_id, actor_name="Ahmed", amount="1000.00")

    submitted = await call(
        "submit_expense",
        company_id=company_id,
        actor_name="Raza",
        role="regular_user",
        amount="250.00",
        description="Office chairs",
    )
    assert submitted["transaction"]["status"] == "pending"
    assert submitted["available"] == "750.00"

    approved = await call(
        "approve_expense",
        transaction_id=submitted["transaction"]["id"],
        actor_name="Ahmed",
    )
    assert approved["transaction"]["status"] == "approved"
    assert approved["balance"] == "750.00"

    report = await call("get_report", company_id=company_id)
    assert report["income"] == "1000.00"
    assert report["expense"] == "250.00"
    assert report["pending_count"] == 0


async def test_rejecting_releases_funds(database):
    company_id = (await call("create_company", name="Acme", owner_name="Ahmed"))[
        "company_id"
    ]
    await call("add_income", company_id=company_id, actor_name="Ahmed", amount="500")
    submitted = await call(
        "submit_expense",
        company_id=company_id,
        actor_name="Raza",
        role="regular_user",
        amount="200",
    )
    rejected = await call(
        "reject_expense",
        transaction_id=submitted["transaction"]["id"],
        actor_name="Ahmed",
        note="not needed",
    )
    assert rejected["transaction"]["status"] == "rejected"
    assert rejected["available"] == "500.00"


async def test_overspending_is_refused(database):
    company_id = (await call("create_company", name="Acme", owner_name="Ahmed"))[
        "company_id"
    ]
    await call("add_income", company_id=company_id, actor_name="Ahmed", amount="100")
    with pytest.raises(Exception, match="exceeds the available balance"):
        await call(
            "submit_expense",
            company_id=company_id,
            actor_name="Ahmed",
            role="admin",
            amount="5000",
        )


async def test_regular_user_cannot_record_income(database):
    company_id = (await call("create_company", name="Acme", owner_name="Ahmed"))[
        "company_id"
    ]
    with pytest.raises(Exception, match="cannot record income"):
        await call(
            "add_income",
            company_id=company_id,
            actor_name="Raza",
            role="regular_user",
            amount="100",
        )


async def test_unknown_role_is_rejected(database):
    company_id = (await call("create_company", name="Acme", owner_name="Ahmed"))[
        "company_id"
    ]
    with pytest.raises(Exception, match="Unknown role"):
        await call(
            "submit_expense",
            company_id=company_id,
            actor_name="Raza",
            role="wizard",
            amount="10",
        )


async def test_anomaly_check_runs_without_any_ai_provider(database):
    """The anomaly tool is statistical, so it must work with no API key."""
    company_id = (await call("create_company", name="Acme", owner_name="Ahmed"))[
        "company_id"
    ]
    await call("add_income", company_id=company_id, actor_name="Ahmed", amount="1000")
    for _ in range(2):
        await call(
            "submit_expense",
            company_id=company_id,
            actor_name="Raza",
            role="admin",
            amount="40",
            description="Same lunch",
        )

    findings = await call("check_for_anomalies", company_id=company_id)
    assert any(f["kind"] == "possible_duplicate" for f in findings)


async def test_list_transactions_filters_by_status(database):
    company_id = (await call("create_company", name="Acme", owner_name="Ahmed"))[
        "company_id"
    ]
    await call("add_income", company_id=company_id, actor_name="Ahmed", amount="900")
    await call(
        "submit_expense",
        company_id=company_id,
        actor_name="Raza",
        role="regular_user",
        amount="60",
    )
    pending = await call("list_transactions", company_id=company_id, status="pending")
    assert len(pending) == 1
    assert pending[0]["amount"] == "60.00"
