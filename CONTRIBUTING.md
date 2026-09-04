# Contributing

Thanks for taking a look. Bug reports and pull requests are both welcome.

## Getting set up

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Tests run against SQLite, so there's nothing to install or configure.

## Before opening a pull request

```bash
ruff check .            # lint
mypy account_manager    # types
pytest                  # tests
```

CI runs all three on Python 3.10–3.12, plus a check that the package
installs cleanly and its migrations run.

## A few things worth knowing

**Business rules belong in `services.py`.** The CLI and the MCP server are
both thin wrappers over it. Putting a rule in one of them means the other
one won't have it, and the two front ends will quietly disagree.

**Balances are derived, never stored.** They're computed by summing the
ledger. Please don't add a cached total column — the whole reason this
project uses a ledger is that stored totals drifted from the transactions
behind them, and could be silently overwritten by a concurrent write.

**Money is `Decimal`, never `float`.** Amounts come in as strings and go
through `services.parse_amount`, including amounts extracted by an AI
model.

**Schema changes need a migration.** People self-host this, so we can't fix
their database by hand — `alembic revision` and make sure `alembic upgrade
head` works on an install that already has data.

**Never build an `Actor` outside `auth.py`.** It carries the authority a
request runs with; the whole point of the auth layer is that it is the only
thing that decides what someone may do. A front end that constructs its own
Actor has silently reintroduced the bug where callers could claim to be
admins.

**If you fix a bug, add a test that fails without the fix.** Several tests
in `tests/test_services.py` are named after the specific problem they
prevent coming back; that's the pattern to follow.

## Areas that need help

- A web interface (authentication now exists to build one on).
- Receipt attachments and recurring transactions.
