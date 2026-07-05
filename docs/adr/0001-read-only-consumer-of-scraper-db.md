---
status: accepted
---

# The Digest Service is a read-only consumer of the Scraper's database, not its own scraper

## Context and decision

The Digest Service originally ingested Telegram messages itself (Telethon user
session) into a git-committed SQLite database and ran on GitHub Actions. A
separate service, the **Scraper** (`clio`), already ingests the same Telegram
chats into a PostgreSQL **Message Store** hosted on Railway. Rather than run two
independent ingestion pipelines against the same chats, we deleted the Digest
Service's ingestion layer entirely and made it a **read-only consumer** of the
Scraper's Message Store: it reads messages for a time window, generates the
Digest via Claude, and posts to Telegram. It now runs as a Railway Cron service.

## Considered options

- **Keep self-scraping** (Telethon + own storage). Rejected: duplicates ingestion
  the Scraper already does, requires a Telegram user session, and keeps two copies
  of the same messages that can drift.
- **Read-only consumer of the Scraper's Message Store** (chosen). No ingestion, no
  Telegram session, single source of truth for messages.

## Consequences

- The Digest Service is **coupled to a schema it does not own**. The Scraper is the
  sole writer and owner of the Message Store (`chats` / `users` / `messages`). The
  Digest Service must treat that schema as read-only: it never writes and never runs
  DDL/migrations against it. A breaking change to the Scraper's schema breaks the
  Digest Service — this dependency is deliberate and must be respected on both sides.
- A future reader will see a "Telegram digest" project with **no Telegram ingestion**.
  That is intentional: ingestion lives in the Scraper (`clio`), not here.
- Availability of the Digest now depends on the availability and freshness of the
  Scraper's Message Store.
