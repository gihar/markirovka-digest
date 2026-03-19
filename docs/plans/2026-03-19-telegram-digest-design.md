# Telegram Digest System — Design Document

> **Status:** Research complete
> **Date:** 2026-03-19
> **Goal:** Ежедневный автоматический дайджест по нескольким Telegram-чатам

---

## Table of Contents

1. [Overview](#overview)
2. [Pipeline Architecture](#1-pipeline-architecture)
3. [Telegram Message Download](#2-telegram-message-download)
4. [Embeddings & Vector Storage](#3-embeddings--vector-storage)
5. [Digest Generation via LLM](#4-digest-generation-via-llm)
6. [GitHub Actions Automation](#5-github-actions-automation)
7. [Delivery & Publishing](#6-delivery--publishing)
8. [Security & Secrets](#7-security--secrets)
9. [Project Structure](#8-project-structure)
10. [Implementation Plan](#implementation-plan)

---

## Overview

### Goals

1. **Автоматический ежедневный дайджест** — кратко о главном из нескольких Telegram-чатов
2. **Тематическая кластеризация** — группировка обсуждений по темам, а не по чатам
3. **Доставка в Telegram + архив в GitHub Issues** — удобно читать + можно искать

### Key Decisions

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| State persistence | Git-committed SQLite (raw messages) | Простота, version history, no external deps |
| Embeddings | Ephemeral per run | $0.001/день, always fresh, no storage bloat |
| LLM | Anthropic Claude Sonnet via API | Best quality for summarization, ~$0.10/day |
| Automation | GitHub Actions cron | Free, integrated, no infra to manage |
| Download | Telethon + StringSession | Full API access, works in CI |
| Delivery | GitHub Issues + Telegram bot | Archive + instant notification |

---

## 1. Pipeline Architecture

> **Expert:** Martin Kleppmann (Distributed Systems), Sam Newman (Architecture)

### Hybrid: Git-Committed State + Ephemeral Embeddings

Два слоя данных:
1. **Persistent** — `messages.db` (SQLite) коммитится в репо. Содержит raw messages + sync cursors. Маленький файл (~100KB-1MB за месяцы).
2. **Ephemeral** — эмбеддинги генерируются каждый запуск для 24ч окна. In-memory sqlite-vec. Не сохраняются.

**Почему не хранить эмбеддинги:**
- 15K messages × 1536 dims × 4 bytes = ~90MB — раздувает репо
- Дайджест использует только сегодняшние сообщения
- Регенерация стоит $0.001 — дешевле хранения
- Обновление модели эмбеддингов не требует ре-индексации

**Pipeline phases:**

```
Phase 1: sync_messages (Python/Telethon)
  → Read sync_state from messages.db
  → Fetch new messages per chat
  → Store in messages.db
  → Commit to repo

Phase 2: prepare_digest (Python/OpenAI)
  → Load today's messages from messages.db
  → Filter (skip short/service messages)
  → Generate embeddings → ephemeral sqlite-vec
  → Cluster topics
  → Output structured JSON

Phase 3: generate_digest (Python/Anthropic)
  → Read structured data
  → Claude generates digest
  → Output markdown

Phase 4: publish
  → Create GitHub Issue
  → Send Telegram message
```

**Idempotency:** Phase 1 uses UNIQUE(chat_id, message_id) — re-runs are safe. Phase 2-4 are stateless.

---

## 2. Telegram Message Download

> **Expert:** Martin Kleppmann (Distributed Systems)

### Telethon + StringSession + Sequential + Backoff

| Decision | Choice |
|----------|--------|
| Session type | StringSession (env var, no file persistence) |
| Rate limiting | Sequential per chat, `flood_sleep_threshold=60`, exponential backoff |
| Filtering | `offset_date`-based, 25h window (buffer for cron drift) |
| Error handling | Circuit breaker: skip chat after 3 failures |
| Message schema | Unified dataclass with optional fields |

**Message dataclass:**
```python
@dataclass(frozen=True)
class TelegramMessage:
    message_id: int
    chat_id: int
    chat_title: str
    sender_name: str | None
    text: str
    date: datetime
    reply_to_message_id: int | None
    content_hash: str  # SHA-256 of text
```

**Risks:**
- Session invalidation → handle `SessionRevokedError`, alert via Telegram bot
- FloodWait > 60s → log warning, circuit breaker skips chat
- Cron drift → 25h window covers up to 1h delay

---

## 3. Embeddings & Vector Storage

> **Expert:** Markus Winand (Database)

### OpenAI text-embedding-3-small + Ephemeral sqlite-vec

**Schema (persistent — messages.db):**
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

CREATE TABLE sync_state (
    chat_id INTEGER PRIMARY KEY,
    last_message_id INTEGER NOT NULL,
    last_sync TEXT NOT NULL
);
```

**Ephemeral (in-memory per run):**
```sql
CREATE VIRTUAL TABLE vec_messages USING vec0(
    message_id INTEGER PRIMARY KEY,
    embedding float[1536] distance_metric=cosine
);
```

**Filtering before embedding:**
- Skip messages < 30 characters
- Skip service messages (joins, leaves, pins)
- Skip pure media without text

**Batching:** Adaptive — 2048 items or ~300K tokens per OpenAI API call.

---

## 4. Digest Generation via LLM

> **Expert:** Theo Browne (API Design)

### Single-pass Claude with Overflow Protection

**Normal path (< 120K tokens):** Все сообщения дня → один запрос к Claude Sonnet → digest.

**Overflow path (> 120K tokens):** Map-reduce — суммаризация per chat (Haiku), затем объединение (Sonnet).

**Prompt template** (`prompts/digest.md`):
- Роль: аналитик по маркировке товаров
- Структура: резюме дня → ключевые темы → нерешённые вопросы → статистика → тренды
- Язык: русский, профессиональный тон
- Правила: не выдумывать, группировать по темам а не по чатам

**Data preparation format:**
```markdown
# Сообщения за 2026-03-19
## Метаданные
- Период: 2026-03-18 09:00 — 2026-03-19 09:00 (MSK)
- Чаты: 5 | Сообщений: 1247

## Чат: Маркировка — Общие вопросы
[09:01] **Иван Петров**: текст сообщения
[09:03] **Мария Сидорова**: текст ответа
```

---

## 5. GitHub Actions Automation

> **Expert:** Kelsey Hightower (DevOps)

### Single Job (simplified from multi-job)

Для проекта такого размера один job достаточен. Все секреты используются в одном step.

```yaml
on:
  schedule:
    - cron: '0 6 * * *'  # 09:00 MSK
  workflow_dispatch:

concurrency:
  group: daily-digest
  cancel-in-progress: false

jobs:
  digest:
    runs-on: ubuntu-latest
    permissions:
      contents: write
      issues: write
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
      - run: uv sync
      - run: uv run python main.py
        env: [all secrets]
      - run: git add data/messages.db && git diff --cached --quiet || git commit -m "chore: update messages" && git push
```

---

## 6. Delivery & Publishing

> **Expert:** Sam Newman (Architecture), Nir Eyal (UX)

### GitHub Issues (archive) + Telegram (notification)

**GitHub Issue:**
- Title: `Дайджест: 2026-03-19`
- Labels: `digest`, `2026-03`
- Body: полный дайджест в markdown

**Telegram message:**
- HTML parse mode (надёжнее MarkdownV2 для юридических текстов)
- Адаптивный формат:
  - Если < 3800 chars → полный текст
  - Если больше → top-5 тем + ссылка на GitHub Issue
- Отправка через Bot API (`httpx`)

---

## 7. Security & Secrets

> **Expert:** Troy Hunt (Security)

| Measure | Implementation |
|---------|---------------|
| Secrets storage | GitHub Repository Secrets |
| Session type | Telethon StringSession (in-memory, never on disk) |
| Token permissions | `contents: write` + `issues: write` only |
| .gitignore | *.session, .env, *.key from day one |
| Startup validation | `config.py` validates all env vars, fails fast |
| Actions pinning | Pin all actions to commit SHAs |
| Concurrency | `concurrency` group prevents parallel runs |

---

## 8. Project Structure

> **Expert:** Sam Newman (Architecture)

Flat Python layout with `uv`:

```
markirovka-digest/
├── pyproject.toml
├── uv.lock
├── .python-version (3.12)
├── .env.example
├── .gitignore
├── channels.toml
├── prompts/digest.md
├── main.py          # entry point (~30 lines)
├── config.py        # env + toml validation (~50 lines)
├── models.py        # dataclasses (~40 lines)
├── db.py            # SQLite operations (~60 lines)
├── downloader.py    # Telethon (~80 lines)
├── embedder.py      # OpenAI + sqlite-vec (~70 lines)
├── analyzer.py      # Anthropic API (~60 lines)
├── publisher.py     # GitHub Issues + Telegram (~70 lines)
├── scripts/generate_session.py
├── data/messages.db
├── tests/
└── .github/workflows/daily-digest.yml
```

**Total: ~460 lines** across 8 modules.

---

## Implementation Plan

### Phase 1: MVP (Core Pipeline)

1. Project setup (pyproject.toml, config, models, db)
2. Downloader (Telethon + StringSession)
3. Analyzer (Claude digest, skip embeddings for MVP)
4. Publisher (GitHub Issues + Telegram)
5. GitHub Actions workflow
6. First manual run

### Phase 2: Embeddings & Clustering

7. Embedder (OpenAI + sqlite-vec topic clustering)
8. Integrate clustering into digest prompt
9. Tests

### Phase 3: Hardening

10. Error notifications
11. Previous-day comparison stats
12. Integration tests

---

## Success Metrics

| Metric | Baseline | Target |
|--------|----------|--------|
| Daily digest delivery | Manual reading | Automated, 09:00 MSK |
| Time to understand day's news | 30-60 min | 2-3 min reading digest |
| Message coverage | Partial (skip chats) | All configured chats |
| Cost per month | $0 | < $5 |
| Failure rate | N/A | < 5% of runs |
