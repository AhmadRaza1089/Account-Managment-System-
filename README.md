# Account Manager

Self-hosted income, expense and approval tracking for small companies —
with an MCP server so you can run your books by talking to an AI assistant.

You bring your own database. Nothing is sent anywhere, nothing is hosted
by anyone else, and the AI features are optional (one of them runs with no
API key at all).

```
$ account-manager expense submit --company 1 --amount 250 --by Raza --role regular_user
#4     expense        250.00 pending   by Raza
Balance: 1000.00  Available: 750.00

$ account-manager expense approve --id 4 --by Ahmed
#4     expense        250.00 approved  by Raza
```

## Try it in 30 seconds

No database to install — it uses a local SQLite file by default.

```bash
pip install git+https://github.com/chahmadraza89/Account-Managment-System-

account-manager init-db
account-manager company create --name "Acme" --owner "Ahmed"
account-manager income add --company 1 --amount 10000 --by Ahmed
account-manager expense submit --company 1 --amount 250 --by Raza --role regular_user
account-manager report --company 1
```

Or with Docker:

```bash
docker compose run --rm app account-manager company create --name Acme --owner Ahmed
```

## What it does

- **Tracks income and expenses** as a ledger of individual transactions,
  each with a category, description, who entered it, and who approved it.
- **Approval workflow** — a regular user's expense waits for an admin to
  approve or reject it; an admin's applies immediately.
- **Won't let you overspend** — expenses are refused if they exceed the
  available balance, and requests awaiting approval reserve their funds so
  the same money can't be committed twice.
- **Balances are always derived** from the ledger, never stored, so they
  can't drift out of sync with the transactions behind them.

## Using your own database

Set `DATABASE_URL`. That's the only required setting, and it has a working
default.

| Database | `DATABASE_URL` | Install |
|---|---|---|
| SQLite (default) | `sqlite:///account_manager.db` | nothing needed |
| MySQL / MariaDB | `mysql+mysqlconnector://user:pass@localhost:3306/account` | `pip install "account-manager[mysql]"` |
| PostgreSQL | `postgresql+psycopg://user:pass@localhost:5432/account` | `pip install "account-manager[postgres]"` |

Copy `.env.example` to `.env` and edit it, or export the variable directly.
Then create the schema:

```bash
alembic upgrade head
```

Run that after every upgrade too — it's how schema changes reach an
existing install without losing data.

## Backing up

You host this, so the data is yours to protect. Back up **before every
upgrade**, since migrations change the schema in place.

```bash
# SQLite — .backup is safe to run while the app is in use, unlike copying the file
sqlite3 account_manager.db ".backup 'backup-$(date +%F).db'"

# MySQL / MariaDB
mysqldump --single-transaction account > "backup-$(date +%F).sql"

# PostgreSQL
pg_dump account > "backup-$(date +%F).sql"
```

Restoring is the reverse: point `DATABASE_URL` at a fresh database, load the
dump, and run `alembic upgrade head`. Test a restore once — an untested
backup isn't a backup.

## Commands

```
account-manager init-db                    Create the tables
account-manager company create|list        Manage companies
account-manager income add                 Record income (admins only)
account-manager expense submit             Spend, or request to spend
account-manager expense approve|reject     Decide a pending request
account-manager report --company 1         Balances and what's awaiting approval
account-manager transactions --company 1   The ledger
account-manager ai log|summary|check       Optional AI features (below)
```

Run any of them with `--help` for the full options.

## Use it from Claude (MCP)

This runs as an [MCP](https://modelcontextprotocol.io) server, so Claude
Desktop, Claude Code, or any MCP client can manage the books directly:
*"what's Acme's balance?"*, *"approve Raza's expense"*, *"anything
suspicious this month?"*

Add it to your client's config (`claude_desktop_config.json`, or
`.mcp.json` for Claude Code):

```json
{
  "mcpServers": {
    "account-manager": {
      "command": "python",
      "args": ["-m", "account_manager.mcp_server"],
      "env": { "DATABASE_URL": "sqlite:////absolute/path/to/account_manager.db" }
    }
  }
}
```

It exposes: `create_company`, `list_companies`, `get_report`, `add_income`,
`submit_expense`, `approve_expense`, `reject_expense`, `list_transactions`,
and `check_for_anomalies`.

## AI features (optional)

**`account-manager ai check` needs no AI provider, no API key, and no
network.** It's statistical, so it also gives the same answer every time —
which matters when the output is effectively a question about someone's
expense claim. It flags probable duplicates, amounts far above the usual
for their category, and requests left waiting too long.

```
$ account-manager ai check --company 1
[high  ] Raza logged 40.00 for 'client lunch' twice within 7 days (#7 and #9).
[medium] #12: 9000.00 for 'meals' is well above the usual 35.00 for that category.
```

The other two need a provider:

```
$ account-manager ai log "paid 45.50 for an uber to the airport" --company 1 --by Raza
Understood: expense 45.50 [travel] Uber to the airport

$ account-manager ai summary --company 1
```

Pick whichever provider suits you — set `AI_PROVIDER` and see `.env.example`:

| Provider | Cost | Install |
|---|---|---|
| `ollama` | free, offline, data never leaves your machine | just [Ollama](https://ollama.com) |
| `claude` | paid API | `pip install "account-manager[claude]"` |
| `openai` | paid API | `pip install "account-manager[openai]"` |

Amounts extracted by a model go through exactly the same validation as
typed input, so a wrong number gets rejected rather than trusted.

## Security — please read before exposing this

**There is no authentication.** Roles (`admin`, `owner`, `regular_user`)
are a workflow convention, not a security boundary: whoever runs the CLI
or reaches the MCP server states their own name and role, so anyone with
access can act as an admin.

That's fine for the intended use — one company, running it on their own
machine or private server. It is **not** safe to expose to the internet or
to untrusted users. Real authentication is the main thing still missing;
see below.

## Not done yet

- Authentication and per-user accounts (see above).
- Multi-company access control — any user of an install can see every
  company in it.
- A web interface. This is a CLI and an MCP server today.
- Receipt scanning, recurring transactions, exports for accountants.

Contributions welcome — these are good places to start.

## Development

```bash
git clone https://github.com/chahmadraza89/Account-Managment-System-
cd Account-Managment-System-
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                      # tests run against SQLite, no setup needed
ruff check .                # lint
mypy account_manager        # types
```

Layout: `models.py` defines the tables, `services.py` holds every business
rule (and is the only place they live), `db.py` manages sessions and
transactions, and `cli.py` and `mcp_server.py` are two thin front ends over
the same services — so the CLI and an AI agent can never disagree about
what the rules are. `ai/` is optional and imported lazily, so the core
never depends on it.

## License

MIT — see [LICENSE](LICENSE).
