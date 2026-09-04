# Changelog

Notable changes, newest first. Versions follow [semantic versioning](https://semver.org).

**Upgrading always means running `alembic upgrade head`** after installing
the new version. Back up your database first (see the README).

## [Unreleased]

### Added

- Transactions record **when the money actually moved**, separately from
  when they were entered, so a receipt from last week can be filed today and
  still land in the right month. Existing rows are backfilled from their
  entry date.
- **Reversal**: an approved entry made by mistake can be undone. The row is
  kept and marked reversed, with who reversed it and why, so the correction
  is part of the record rather than a deletion. Reversing income can leave
  the balance negative — that is the true position, and further spending
  stays blocked until it recovers.
- **Per-company currency** (display only; there is no conversion between
  currencies).
- **Date-range reporting** and filtering, and **CSV export** for handing the
  ledger to an accountant.
- Pagination for companies with long histories.

### Fixed

- Two admins approving the same expense at the same moment could both apply
  it, spending the money twice. The status check and the status change now
  happen under one lock, with a locking re-read so the second approver sees
  the first one's result.
- On MySQL, a balance check that followed any other read in the same
  transaction used a frozen snapshot, so two people could each spend the
  full balance. MySQL connections are now pinned to READ COMMITTED, matching
  PostgreSQL and SQLite.

### Added

- The test suite runs against real PostgreSQL and MySQL servers in CI, not
  only SQLite. Row locking is a no-op on SQLite, so the protection against
  two people spending the same money was previously never exercised — both
  bugs above were found this way.
- Concurrency tests that deliberately widen the race window, so they fail if
  the locking is ever removed.
- CI builds and runs the Docker image.
- A release workflow that publishes to PyPI on a version tag.

## [0.2.0] — 2026-09-04

The project was a single script before this release. Everything below is new
or rebuilt.

### Fixed

- Pending expenses could never be approved or rejected — money entered
  `pending_expense` and had no path out. Approve and reject now exist.
- An approved admin had no spending limit at all and could drive the balance
  arbitrarily negative. Expenses are now checked against the available
  balance for every role.
- The overspend check compared against gross income, ignoring what had
  already been spent or was awaiting approval.
- Concurrent writes silently overwrote each other, because balances were
  stored totals that were read, modified and written back whole.
- Creating a company committed in two steps, so a failure halfway left an
  unusable half-created record.
- Permission checks were unreachable: the CLI and MCP tools accepted a role
  and then used admin internally regardless.
- `Account` rows were never inserted, so every balance update silently
  matched zero rows.
- A hardcoded database password was removed from the source.

### Added

- Money is a ledger of individual transactions. Balances are derived by
  summing it, so they cannot drift, and pending expenses reserve their funds.
- Full audit trail: category, description, who created a transaction, who
  decided it, and when.
- Any database via a single `DATABASE_URL`, defaulting to SQLite so the
  project runs with no database setup. MySQL and PostgreSQL are extras.
- Alembic migrations, including converting an existing install's stored
  totals into ledger entries without losing data.
- An MCP server, so the books can be managed from Claude Desktop, Claude
  Code, or any MCP client.
- Optional AI features: natural-language transaction entry and summaries via
  Claude, OpenAI or Ollama, plus anomaly detection that needs no AI provider
  at all.
- A real CLI (`account-manager`), pip-installable, plus Docker and compose.
- MIT licence. The repository was public but unlicensed, so nobody had
  permission to use it.
