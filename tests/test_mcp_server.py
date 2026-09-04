import pytest

from account_manager.mcp_server import mcp

EXPECTED_TOOLS = {
    "create_company",
    "list_companies",
    "get_report",
    "add_income",
    "submit_expense",
}


@pytest.mark.asyncio
async def test_all_expected_tools_are_registered():
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert EXPECTED_TOOLS <= names


@pytest.mark.asyncio
async def test_tools_have_descriptions():
    tools = await mcp.list_tools()
    for tool in tools:
        assert tool.description, f"Tool {tool.name} is missing a description."
