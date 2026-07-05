"""Gold standard: read-only PostgreSQL access with parameterized queries.

Rules:
- Read-only: this service never writes to the Message Store and never runs DDL
  against it (the schema is owned by the scraper — see ADR-0001).
- Use parameterized queries exclusively (%(name)s placeholders, never f-strings).
- Return frozen dataclasses from queries, not raw rows.
- One short-lived connection per run (psycopg 3); no global state.
- Depend defensively on optional scraper tables: probe with to_regclass and add
  the clause only when present, so a not-yet-deployed table can't break the read.
Extracted from db.py.
"""

from collections.abc import Sequence
from datetime import date

import psycopg

from models import TelegramMessage

# {spam_filter} is filled only when the scraper's optional spam_users table
# exists; the clause is a fixed literal (no interpolated data).
_QUERY_TEMPLATE = """
SELECT
    c.title                                        AS chat_title,
    COALESCE(u.username, u.first_name, 'Unknown')  AS sender_name,
    COALESCE(m.text, m.caption)                    AS content,
    m.sent_at                                      AS sent_at,
    m.chat_id                                      AS chat_id
FROM messages m
JOIN chats c ON c.id = m.chat_id
LEFT JOIN users u ON u.id = m.user_id
WHERE m.chat_id = ANY(%(chat_ids)s)
  AND (m.sent_at AT TIME ZONE 'Europe/Moscow')::date = %(day)s
  AND COALESCE(m.text, m.caption) IS NOT NULL
  AND char_length(COALESCE(m.text, m.caption)) >= %(min_length)s
  AND u.is_bot IS NOT TRUE
  {spam_filter}
ORDER BY c.title, m.sent_at
"""

_SPAM_EXCLUSION = """
  AND NOT EXISTS (
      SELECT 1 FROM spam_users su
      WHERE su.chat_id = m.chat_id AND su.user_id = m.user_id
  )"""


def _spam_exclusion(conn: psycopg.Connection) -> str:
    """Return the spam clause iff the scraper's spam_users table exists."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('spam_users')")
        return _SPAM_EXCLUSION if cur.fetchone()[0] is not None else ""


def fetch_digest_messages(
    conn: psycopg.Connection,
    chat_ids: Sequence[int],
    day: date,
    min_length: int,
) -> list[TelegramMessage]:
    """Return messages for ``day`` (Europe/Moscow) across the allow-listed chats."""
    query = _QUERY_TEMPLATE.format(spam_filter=_spam_exclusion(conn))
    with conn.cursor() as cur:
        cur.execute(
            query,
            {"chat_ids": list(chat_ids), "day": day, "min_length": min_length},
        )
        rows = cur.fetchall()
    return [
        TelegramMessage(
            chat_id=chat_id,
            chat_title=chat_title or "",
            sender_name=sender_name,
            text=content,
            date=sent_at,
        )
        for chat_title, sender_name, content, sent_at, chat_id in rows
    ]
