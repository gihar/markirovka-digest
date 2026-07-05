# markirovka-digest

Daily digest of several Telegram chats about Russian product labeling
(маркировка — Честный Знак, ЦРПТ). Once a day it reads the previous day's
messages, summarizes them with Claude, and posts the result to a Telegram
channel.

## Architecture

This is the **Digest Service** — a **read-only consumer** (see
[ADR-0001](docs/adr/0001-read-only-consumer-of-scraper-db.md)). It does **not**
scrape Telegram. A separate **Scraper** (`clio`) continuously ingests messages
into a PostgreSQL **Message Store** on Railway; this service only reads from it.

```
clio (scraper, always-on) ──writes──▶ PostgreSQL (Message Store) ◀──reads (read-only)── markirovka-digest (Railway Cron, 09:00 MSK)
                                       chats / users / messages                               │
                                                                              LLM (OpenAI-compat) ◀─┘
                                                                                       │
                                                                                       ▼
                                                                    Telegram: digest channel (poster bot)
```

Domain vocabulary is in [`CONTEXT.md`](CONTEXT.md).

### Pipeline (`main.py`, synchronous, run-once)

1. Compute the **Digest Window**: the previous calendar day in `Europe/Moscow`.
2. Read messages for that day across the allow-listed chats from the Message Store.
3. If there are none, log and exit (nothing is posted on a quiet day).
4. Otherwise generate the digest via the LLM and post it to the Telegram channel,
   splitting into several messages if it exceeds Telegram's 4096-char limit.

| Module | Responsibility |
|--------|----------------|
| `config.py` | Env vars + the chat allow-list (`channels.toml`) |
| `window.py` | The previous-MSK-day helper |
| `db.py` | Read-only PostgreSQL access (psycopg 3) |
| `analyzer.py` | Format messages, call the LLM (OpenAI-compatible) |
| `publisher.py` | Telegram-only delivery with message splitting |
| `main.py` | Orchestration |

## Configuration

Environment variables (see [`.env.example`](.env.example)):

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | The scraper's PostgreSQL connection string (read-only use) |
| `LLM_BASE_URL` | OpenAI-compatible base URL up to `/v1` (e.g. `https://openrouter.ai/api/v1`) |
| `LLM_API_KEY` | Bearer token for the LLM endpoint |
| `LLM_MODEL` | Provider model id (e.g. `anthropic/claude-sonnet-4.6`) |
| `TELEGRAM_BOT_TOKEN` | Bot that posts the digest |
| `TELEGRAM_DIGEST_CHAT_ID` | Destination channel/chat id |

The chats to digest are pinned in [`channels.toml`](channels.toml) as an explicit
allow-list of `chat_id`s (titles are read from the Message Store). Digest window
length and the minimum message length live under `[settings]` there.

## Run locally

```bash
uv sync
uv run python main.py    # reads env vars; posts the previous MSK day's digest
uv run pytest            # tests (db tests spin up an ephemeral Postgres via Docker)
```

## Deploy (Railway Cron)

Runs as its own service in the **same Railway project** as the scraper and its
PostgreSQL, so it reaches the database over the private network.

1. Create a service from this repo.
2. Set the environment variables above. Point `DATABASE_URL` at the PostgreSQL
   service (e.g. reference `${{Postgres.DATABASE_URL}}` so it uses the private
   `postgres.railway.internal` host).
3. Set the **Cron Schedule** to `0 6 * * *` (09:00 MSK). [`railway.json`](railway.json)
   also declares this; if Railway's config-as-code cron field differs from your
   Railway version, set it in the service **Settings → Cron Schedule** instead.

The process runs to completion and exits; the restart policy is `NEVER` so a
finished (or failed) run is not restarted until the next scheduled tick — a
missed day is simply skipped (no self-healing, by design).
