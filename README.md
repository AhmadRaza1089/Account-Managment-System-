# Account Management System

A small company income/expense tracker with a lightweight admin-approval
workflow, backed by MySQL.

> **Security note:** an earlier version of this repository had a MySQL
> password hardcoded in source and committed to git history. If that
> password is still in use anywhere, **rotate it now** — anyone with
> access to this repo's history has it.

## Project layout

```
account_manager/
  config.py      # environment-based settings (DatabaseSettings)
  db.py          # connection pool + schema creation
  models.py      # domain logic (Company, Account, User, ExpenseManager) — no DB calls, unit-testable
  repository.py  # persistence layer, translates domain objects <-> MySQL
  main.py        # entry point / demo script (guarded by __main__)
  mcp_server.py  # MCP server — lets AI agents manage accounts directly
tests/
  test_models.py     # unit tests for business rules, no live database required
  test_mcp_server.py # checks the MCP tool registry
```

## Setup

1. Create and activate a virtualenv, then install dependencies:
   ```
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements-dev.txt
   ```
2. Copy `.env.example` to `.env` and fill in your real MySQL credentials.
   `.env` is gitignored — never commit real credentials.
3. Run the demo:
   ```
   python -m account_manager.main
   ```

## Using it as an MCP server

This project can run as a [Model Context Protocol](https://modelcontextprotocol.io)
server, so you can manage a company's accounts by talking to Claude
Desktop, Claude Code, or any other MCP-compatible client — instead of
calling a CLI or writing SQL.

It exposes 5 tools: `create_company`, `list_companies`, `get_report`,
`add_income`, `submit_expense`. They wrap the same `models`/`repository`
code the CLI uses, so behavior (role checks, pending-expense rules) is
identical either way.

1. Install dependencies and set up `.env` as in Setup above.
2. Add it to your MCP client's config. For Claude Desktop
   (`claude_desktop_config.json`) or Claude Code (`.mcp.json`):
   ```json
   {
     "mcpServers": {
       "account-manager": {
         "command": "python",
         "args": ["-m", "account_manager.mcp_server"],
         "cwd": "/absolute/path/to/this/repo",
         "env": {
           "DB_HOST": "localhost",
           "DB_USER": "your_user",
           "DB_PASSWORD": "your_password",
           "DB_NAME": "account"
         }
       }
     }
   }
   ```
3. Restart the client. You can then ask it things like "create a company
   called Acme owned by Ahmed" or "what's Acme's current balance?" and it
   will call the tools above.

> There's no authentication layer yet — the caller states who they are
> (name/role) as plain tool arguments, so treat this as a trusted,
> single-tenant setup for now (see Roadmap).

## Testing

```
pytest
```

Model/business-rule tests run against plain Python objects (no database
required). Lint and type-check with:

```
ruff check .
mypy account_manager
```

All three run in CI on every push (see `.github/workflows/ci.yml`).

## What changed from the original single-file script

- Removed the hardcoded DB password; credentials now come from environment
  variables (`.env`, loaded via `python-dotenv`).
- Fixed a bug where `Account` rows were never actually inserted into the
  database — only `Company` was, so every subsequent `UPDATE Account ...`
  silently matched zero rows. Persisted state and the in-memory objects
  could diverge.
- Fixed a bug where a regular user's expense request never updated
  `pending_expense`, so pending requests were only tracked for
  not-yet-approved admins, not for regular users.
- Added `try/except` + rollback around all database writes, and a
  connection pool instead of a single shared global connection/cursor
  (the original was not safe under any concurrent use).
- Split the single file into config / db / models / repository / entry
  point, so business rules can be unit-tested without a database.
- Added `.gitignore`, pinned dependency files, CI (lint + type-check +
  test on every push).

## Roadmap (not done yet)

This hardens the existing script; it is still a single-tenant demo, not a
sellable product. To get there:

- Real authentication (hashed passwords, sessions/JWT) — `User` objects
  are currently just plain data, not tied to a login flow. This also
  means the MCP server has no per-caller auth yet — see the note above.
- Expense requests as first-class records (id, requester, approver,
  timestamps, status) instead of a running `pending_expense` total.
- Multi-tenancy enforced at the query layer.
- Audit log of all financial changes.
- Natural-language expense entry and AI-generated summaries, built on top
  of the MCP tools above.
- A web API (e.g. FastAPI) and frontend — this is currently a script/CLI.
- Deployment (Docker, managed DB, CI/CD to a host), observability
  (structured logs, error tracking), and a security review before
  handling real customer data.
