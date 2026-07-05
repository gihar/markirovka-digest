# Context: markirovka-digest

Glossary of domain terms for the daily Telegram digest system. This file is a
glossary only — no implementation details.

## Terms

### Scraper (Scraper Bot)
A **separate**, independently-running service (not part of this repository) that
continuously ingests messages from Telegram chats and writes them into the
**Message Store**. It is the sole owner and writer of the Message Store schema.

### Message Store
The PostgreSQL database (hosted on Railway) that holds raw Telegram messages.
Owned and written exclusively by the **Scraper**. From the perspective of the
**Digest Service** it is **read-only**: the Digest Service never writes to it and
never alters its schema.

### Digest Service
This application. A **read-only consumer** of the Message Store. Its
responsibility: read messages for a time window, generate a summary
(the **Digest**) via Claude, and publish it. It does not ingest from Telegram
and holds no user Telegram session.

### Digest
The generated summary (Markdown) of chat activity over a time window, produced
by Claude and delivered to its publish targets. **Cross-chat** (spans multiple
Monitored Chats) and **scheduled** (produced once per day). Distinct from a
Chat Summary.

### Chat Summary
A **single-chat**, **on-demand** summary produced by the Scraper itself (clio)
via its own LLM path. Not part of the Digest Service. Named here only to keep
the boundary explicit: the Digest Service never produces Chat Summaries, and the
Scraper never produces the Digest.

### Digest Window
The time span of messages included in one Digest run: the **previous calendar
day** in the **Europe/Moscow** timezone (00:00–23:59:59 MSK of the day before
the run). A run does not "self-heal" a previously missed day — each run covers
exactly one calendar day.

### Monitored Chat
A Telegram chat whose messages are eligible to appear in a Digest. Identified by
a chat id.
