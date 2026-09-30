"""Read-only access to the Scraper's Message Store (PostgreSQL).

The Digest Service is a read-only consumer (see ADR-0001): this module only
SELECTs. It never writes and never runs DDL against the Store, whose schema is
owned by the Scraper (clio).

Uses psycopg (v3) with a single short-lived connection — enough for a once-a-day
cron run. Parameterized queries only.
"""

from collections.abc import Sequence
from datetime import date

import psycopg

from digest_archive import ChannelPost
from models import TelegramMessage

# Author display name falls back username -> first_name -> 'Unknown'.
# Content is the text, or the media caption when there is no text.
# The Digest Window is bucketed by the Europe/Moscow calendar date: converting a
# timestamptz with a single AT TIME ZONE yields the naive Moscow wall-clock, and
# ::date on that is the Moscow calendar day (independent of session timezone).
# Bot-authored messages are excluded (u.is_bot IS NOT TRUE also keeps rows with
# no user, where the LEFT JOIN yields NULL).
# Posts originating in the Digest Channel are excluded: Telegram auto-forwards
# them into the linked Monitored Chat from a service account that is not flagged
# is_bot, so the forward origin is what identifies them. IS DISTINCT FROM (not
# !=) is required — messages nobody forwarded carry NULL there, and NULL != x is
# NULL, which would drop nearly every real message.
# {spam_filter} is filled with the spam-user exclusion iff the Scraper's
# spam_users table is present (see _spam_exclusion); the clause is a fixed
# literal with no interpolated data.
_QUERY_TEMPLATE = """
SELECT
    c.title                                        AS chat_title,
    COALESCE(u.username, u.first_name, 'Unknown')  AS sender_name,
    COALESCE(m.text, m.caption)                    AS content,
    m.sent_at                                      AS sent_at,
    m.chat_id                                      AS chat_id,
    m.message_id                                   AS message_id,
    c.username                                     AS chat_username,
    m.reply_to_message_id                          AS reply_to_message_id
FROM messages m
JOIN chats c ON c.id = m.chat_id
LEFT JOIN users u ON u.id = m.user_id
WHERE m.chat_id = ANY(%(chat_ids)s)
  AND (m.sent_at AT TIME ZONE 'Europe/Moscow')::date = %(day)s
  AND COALESCE(m.text, m.caption) IS NOT NULL
  AND char_length(COALESCE(m.text, m.caption)) >= %(min_length)s
  AND u.is_bot IS NOT TRUE
  AND m.forward_from_chat_id IS DISTINCT FROM %(digest_channel_id)s
  {spam_filter}
ORDER BY c.title, m.sent_at
"""

# Chat-scoped, whole-user spam exclusion — the predicate clio (the Scraper)
# documents for consumers of its spam_users table.
_SPAM_EXCLUSION = """
  AND NOT EXISTS (
      SELECT 1 FROM spam_users su
      WHERE su.chat_id = m.chat_id AND su.user_id = m.user_id
  )"""


def connect(database_url: str) -> psycopg.Connection:
    """Open a connection to the Message Store."""
    return psycopg.connect(database_url)


def _row_to_message(row: tuple) -> TelegramMessage:
    """Map a query row to a frozen TelegramMessage."""
    (chat_title, sender_name, content, sent_at, chat_id, message_id, username,
     reply_to) = row
    return TelegramMessage(
        chat_id=chat_id,
        chat_title=chat_title or "",
        sender_name=sender_name,
        text=content,
        date=sent_at,
        message_id=message_id,
        chat_username=username,
        reply_to_message_id=reply_to,
    )


def _spam_exclusion(conn: psycopg.Connection) -> str:
    """Return the spam clause iff the Scraper's spam_users table exists.

    The Scraper owns spam_users and may not have deployed it yet; referencing a
    missing table would fail the whole read. Probing keeps the Digest Service
    working before the table lands and turns the filter on automatically after.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('spam_users')")
        return _SPAM_EXCLUSION if cur.fetchone()[0] is not None else ""


def fetch_digest_messages(
    conn: psycopg.Connection,
    chat_ids: Sequence[int],
    day: date,
    min_length: int,
    digest_channel_id: int,
) -> list[TelegramMessage]:
    """Return messages for ``day`` (Europe/Moscow) across the allow-listed chats.

    Filters out empty/too-short content, bot authors, spam-flagged users (when
    the Scraper's spam_users table is available), non-allow-listed chats, and
    posts forwarded from the Digest Channel (``digest_channel_id``) — otherwise
    the Digest would summarise its own previous output. Ordered by chat title,
    then time.
    """
    query = _QUERY_TEMPLATE.format(spam_filter=_spam_exclusion(conn))
    with conn.cursor() as cur:
        cur.execute(
            query,
            {
                "chat_ids": list(chat_ids),
                "day": day,
                "min_length": min_length,
                "digest_channel_id": digest_channel_id,
            },
        )
        rows = cur.fetchall()
    return [_row_to_message(row) for row in rows]


# Digest Channel posts forwarded into the allow-listed chats from ``since`` (an
# Europe/Moscow calendar day) on, in publication order. Read only to find
# published Digests (ADR-0003) — never as discussion input.
_CHANNEL_POSTS_QUERY = """
SELECT m.chat_id, COALESCE(m.text, m.caption)
FROM messages m
WHERE m.chat_id = ANY(%(chat_ids)s)
  AND m.forward_from_chat_id = %(digest_channel_id)s
  AND (m.sent_at AT TIME ZONE 'Europe/Moscow')::date >= %(since)s
  AND COALESCE(m.text, m.caption) IS NOT NULL
ORDER BY m.sent_at, m.chat_id, m.message_id
"""


def fetch_channel_posts(
    conn: psycopg.Connection,
    chat_ids: Sequence[int],
    since: date,
    digest_channel_id: int,
) -> list[ChannelPost]:
    """Return the Digest Channel's posts forwarded into the allow-listed chats.

    Only posts sent on ``since`` (Europe/Moscow) or later. These are where
    published Digests live; see digest_archive.published_digest.
    """
    with conn.cursor() as cur:
        cur.execute(
            _CHANNEL_POSTS_QUERY,
            {
                "chat_ids": list(chat_ids),
                "digest_channel_id": digest_channel_id,
                "since": since,
            },
        )
        rows = cur.fetchall()
    return [ChannelPost(chat_id=chat_id, text=text) for chat_id, text in rows]
