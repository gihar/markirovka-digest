# Decisions Log — Telegram Daily Digest (MVP)

## Feature Definition of Done
- uv run python main.py works locally
- All modules have type hints
- Frozen dataclasses throughout
- No hardcoded secrets
- Files < 800 lines, functions < 50 lines
- GitHub Actions workflow is valid YAML

## Risks & Mitigations
(Pending risk analysis phase)

## Architectural Decisions

## Decision: Flat module layout with no package — simplicity for ~300 lines
Date: 2026-03-20
Context: Greenfield project with 7 small modules. Design doc specifies flat layout.
Alternatives considered: src/ package layout (overkill for MVP), domain-based folders (unnecessary at this scale)

## Decision: Git-committed messages.db — persistent state without external DB
Date: 2026-03-20
Context: Design doc specifies SQLite committed to repo. File stays small (text-only, no embeddings).
Alternatives considered: External DB (too heavy), ephemeral state (loses history)

## Decision: MVP excludes embeddings, sqlite-vec, clustering — Phase 2 items
Date: 2026-03-20
Context: Brief explicitly excludes these. Claude handles topic grouping naturally from raw messages.
Alternatives considered: Including embeddings now (adds complexity, cost, and deps for marginal MVP value)
