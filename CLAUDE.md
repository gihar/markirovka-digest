# markirovka-digest

Ежедневный дайджест чатов по маркировке: читает Message Store скрапера,
генерирует сводку через LLM и публикует в Telegram. Доменный словарь —
`CONTEXT.md`, архитектурные решения — `docs/adr/`.

## Agent skills

### Issue tracker

GitHub Issues в `gihar/markirovka-digest` через `gh` CLI; внешние PR каналом
входящих запросов не считаются. См. `docs/agents/issue-tracker.md`.

### Triage labels

Канонический словарь без переименований: `needs-triage`, `needs-info`,
`ready-for-agent`, `ready-for-human`, `wontfix`.
См. `docs/agents/triage-labels.md`.

### Domain docs

Одноконтекстный репозиторий: `CONTEXT.md` и `docs/adr/` в корне.
См. `docs/agents/domain.md`.
