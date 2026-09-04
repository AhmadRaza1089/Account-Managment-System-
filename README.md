# Account Manager

Self-hosted income, expense and approval tracking for small companies —
with an MCP server so you can run your books by talking to an AI assistant.

You bring your own database. Nothing is sent anywhere, nothing is hosted
by anyone else, and the AI features are optional (one of them runs with no
API key at all).

```
$ account-manager expense submit --company 1 --amount 250 --description "Office chairs"
#4     2026-09-04 expense        250.00 pending   by raza
Balance: 1000.00  Available: 750.00

$ account-manager expense approve --id 4          # as an admin
#4     2026-09-04 expense        250.00 approved  by raza
```

## Try it in 30 seconds

No database to install — it uses a local SQLite file by default.

```bash
pip install git+https://github.com/chahmadraza89/Account-Managment-System-

account-manager init-db
account-manager user create --username you --superuser
account-manager login --username you

account-manager company create --name "Acme" --owner "Ahmed"
account-manager income add --company 1 --amount 10000
account-manager expense submit --company 1 --amount 250
account-manager report --company 1
```

Or with Docker:

```bash
docker compose run --rm -e ACCOUNT_MANAGER_PASSWORD=choose-a-password \
    app account-manager user create --username you --superuser
docker compose run --rm -e ACCOUNT_MANAGER_PASSWORD=choose-a-password \
    app account-manager login --username you
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
- **Records when the money moved**, separately from when someone typed it
  in, so you can enter last week's receipt today and still report on the
  right month.
- **Corrects mistakes without erasing them** — reversing an entry keeps it
  in the ledger marked reversed, with who reversed it and why.
- **Exports to CSV** for your accountant, and reports on any date range.

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
account-manager user create|list|passwd    Manage accounts
account-manager login | logout | whoami    Sign in and out
account-manager token create               A credential for the MCP server
account-manager company create|list        Manage companies
account-manager member add|remove|list     Who can use a company
account-manager income add                 Record income (admins only)
account-manager expense submit             Spend, or request to spend
account-manager expense approve|reject     Decide a pending request
account-manager expense reverse            Undo an approved entry made by mistake
account-manager report --company 1         Balances and what's awaiting approval
account-manager transactions --company 1   The ledger
account-manager export --company 1         The ledger as CSV
account-manager ai log|summary|check       Optional AI features (below)
```

Run any of them with `--help` for the full options.

```bash
# Enter a receipt from last week, in your own currency
account-manager company create --name "Acme" --owner Ahmed --currency PKR
account-manager expense submit --company 1 --amount 1200 \
    --date 2026-01-22 --category office --description "Desks"

# Wrong amount? Correct it — the original stays on the record
account-manager expense reverse --id 2 --reason "typo, should be 120"

# How did January go, and give me the CSV
account-manager report --company 1 --since 2026-01-01 --until 2026-01-31
account-manager export --company 1 --output january.csv
```

## Use it from Claude (MCP)

This runs as an [MCP](https://modelcontextprotocol.io) server, so Claude
Desktop, Claude Code, or any MCP client can manage the books directly:
*"what's Acme's balance?"*, *"approve Raza's expense"*, *"anything
suspicious this month?"*

First create a credential for it:

```bash
account-manager token create --name mcp
```

Then add it to your client's config (`claude_desktop_config.json`, or
`.mcp.json` for Claude Code):

```json
{
  "mcpServers": {
    "account-manager": {
      "command": "python",
      "args": ["-m", "account_manager.mcp_server"],
      "env": {
        "DATABASE_URL": "sqlite:////absolute/path/to/account_manager.db",
        "ACCOUNT_MANAGER_TOKEN": "the-token-you-just-created"
      }
    }
  }
}
```

The server acts as whichever account that token belongs to, with exactly
that account's companies and permissions. Give an assistant a
`regular_user` account and it can request spending but not approve it.

It exposes: `create_company`, `list_companies`, `get_report`, `add_income`,
`submit_expense`, `approve_expense`, `reject_expense`, `reverse_transaction`,
`list_transactions`, `export_ledger_csv`, and `check_for_anomalies`.

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
$ account-manager ai log "paid 45.50 for an uber to the airport" --company 1
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

## Accounts and permissions

Everyone logs in, and your role comes from your account — not from a flag
you type — so nobody can grant themselves admin rights.

```bash
account-manager user create --username ahmed --superuser   # the first account
account-manager login --username ahmed

account-manager user create --username raza
account-manager member add --company 1 --username raza --role regular_user
```

| Role | Can do |
|---|---|
| `admin` / `owner` | Record income, approve and reject requests, reverse mistakes, manage members |
| `regular_user` | Request spending, which then waits for an admin |
| superuser | All of the above, in every company; manages accounts |

Access is **per company**, so one install can hold several companies
without everyone seeing all of them. A company you're not a member of
reports as "not found" rather than "forbidden", so the install doesn't leak
that other companies exist.

Passwords are hashed with scrypt and never stored in readable form. Logging
in saves a token to `~/.config/account-manager/credentials.json`, readable
only by you; changing a password or disabling an account immediately
invalidates every existing login.

### Still worth knowing

There is no transport security, because there is no network service — this
is a CLI and a local MCP server, both talking straight to your database. If
you ever put a web front end on it, that is where TLS and rate limiting
would need to go.

## Not done yet

- A web interface. This is a CLI and an MCP server today.
- Password reset by email, and two-factor authentication.
- Receipt attachments, recurring transactions, and multi-currency
  conversion (each company has one currency; there are no exchange rates).

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
