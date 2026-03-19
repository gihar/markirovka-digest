# Feature Brief: Telegram Daily Digest (MVP)

## Intent
Build an automated daily digest system that downloads messages from multiple Telegram chats about product labeling (markirovka), summarizes them via Claude LLM, and delivers the digest to the user via GitHub Issues (archive) and Telegram bot (notification). The user currently spends 30-60 minutes reading chats manually — this should reduce to 2-3 minutes reading a structured digest.

## Audience
The project owner — a professional tracking Russian product labeling regulations (Chestniy Znak, CRPT). Solo user initially, but the system should be reusable for other domains by changing the prompt file.

## Success Criteria
1. **Messages download**: Telethon connects via StringSession, downloads messages from configured chats for the last 25 hours, stores them in SQLite (`data/messages.db`)
2. **Digest generation**: Claude Sonnet generates a structured Russian-language digest with: daily summary, key topics, unresolved questions, activity stats
3. **GitHub Issue created**: Issue titled "Дайджест: YYYY-MM-DD" with labels `digest` and `YYYY-MM`, containing full digest in markdown
4. **Telegram notification sent**: Bot sends a summary message to a configured chat (HTML parse mode, adaptive: full text if <3800 chars, otherwise top topics + Issue link)
5. **GitHub Actions workflow**: Runs daily at 09:00 MSK (cron `0 6 * * *`), also triggerable manually via `workflow_dispatch`
6. **Config-driven**: Chat list in `channels.toml`, prompt in `prompts/digest.md`, secrets in GitHub Secrets
7. **Idempotent**: Re-runs don't create duplicate messages (UNIQUE constraint on chat_id + message_id)
8. **`uv run python main.py`** works locally with `.env` file

## Exclusions
- **NO embeddings or vector search** (Phase 2, not now)
- **NO sqlite-vec** (not needed for MVP)
- **NO topic clustering** (Claude handles this naturally from raw messages)
- **NO previous-day comparison** (Phase 3)
- **NO integration tests** (unit tests only for MVP)
- **NO multi-job GitHub Actions** — single job is sufficient

## Additional Context
- Prompt is domain-specific (markirovka/labeling), but stored in a separate file (`prompts/digest.md`) so it can be swapped for other domains
- The user confirmed the prompt should reference: Chestniy Znak, CRPT, regulatory changes, product categories
- Overflow protection: if messages exceed 120K tokens, switch to map-reduce (per-chat Haiku summaries → Sonnet merge). But this is edge-case logic, not the primary path.
- Telethon rate limiting: sequential per chat, `flood_sleep_threshold=60`, circuit breaker skips after 3 failures
- Messages.db is git-committed (small file, text-only, no embeddings)
- StringSession stored as GitHub Secret `TELEGRAM_SESSION`

## Project Context
- **Stack**: Python 3.12 + uv
- **Greenfield**: No existing code. Only `docs/plans/` and `.claude/` exist
- **Dependencies**: telethon, anthropic, tiktoken, httpx, tomllib (stdlib)
- **Flat layout**: `main.py`, `config.py`, `models.py`, `db.py`, `downloader.py`, `analyzer.py`, `publisher.py`
- **~300 lines total** across 7 modules (embedder.py excluded from MVP)
- **Design doc**: `docs/plans/2026-03-19-telegram-digest-design.md` — comprehensive reference for all architectural decisions

## Key Files from Design Doc

| File | Purpose | Lines |
|------|---------|-------|
| `pyproject.toml` | uv project config, dependencies | ~30 |
| `channels.toml` | Chat IDs and titles to monitor | ~15 |
| `prompts/digest.md` | Claude prompt template (Russian) | ~40 |
| `main.py` | Entry point, pipeline orchestrator | ~30 |
| `config.py` | Env vars + TOML validation, fail-fast | ~50 |
| `models.py` | Frozen dataclasses: TelegramMessage, DigestResult | ~40 |
| `db.py` | SQLite init, message CRUD, sync_state | ~60 |
| `downloader.py` | Telethon: StringSession, sequential, backoff | ~80 |
| `analyzer.py` | Anthropic API: prepare markdown, call Claude | ~60 |
| `publisher.py` | GitHub Issues (gh CLI) + Telegram bot (httpx) | ~70 |
| `scripts/generate_session.py` | One-time: interactive StringSession generation | ~30 |
| `.github/workflows/daily-digest.yml` | Cron workflow | ~40 |
| `.gitignore` | Exclude .env, *.session, __pycache__ | ~10 |
| `.env.example` | Template for local development | ~10 |

## Database Schema
```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    chat_title TEXT,
    sender_name TEXT,
    text TEXT NOT NULL,
    date TEXT NOT NULL,
    reply_to_message_id INTEGER,
    content_hash TEXT NOT NULL,
    UNIQUE(chat_id, message_id)
);
CREATE INDEX idx_messages_date ON messages(date);
CREATE INDEX idx_messages_chat ON messages(chat_id);

CREATE TABLE sync_state (
    chat_id INTEGER PRIMARY KEY,
    last_message_id INTEGER NOT NULL,
    last_sync TEXT NOT NULL
);
```

---

## Review Checklist (for code reviewers)

- [ ] Messages download from Telegram via Telethon with StringSession
- [ ] Sequential download with flood_sleep_threshold=60 and circuit breaker
- [ ] Messages stored in SQLite with UNIQUE(chat_id, message_id) — idempotent
- [ ] Config loaded from channels.toml + env vars, validated at startup
- [ ] Claude Sonnet generates structured digest in Russian
- [ ] Prompt stored in prompts/digest.md, domain-specific but swappable
- [ ] Token counting with overflow protection (120K threshold)
- [ ] GitHub Issue created with correct title, labels, and body
- [ ] Telegram message sent via Bot API with HTML parse mode
- [ ] Adaptive Telegram format: full text or summary+link based on length
- [ ] GitHub Actions workflow with cron + workflow_dispatch
- [ ] All secrets via env vars, never hardcoded
- [ ] .gitignore covers .env, *.session, *.db (except data/messages.db)
- [ ] Frozen dataclasses (immutability)
- [ ] Files under 800 lines, functions under 50 lines
- [ ] Exclusions respected: NO embeddings, NO sqlite-vec, NO clustering, NO multi-job Actions
