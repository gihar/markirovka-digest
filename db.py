"""SQLite operations for message storage and sync state.

All functions take a Connection as the first argument (no global state).
Returns frozen TelegramMessage dataclasses from queries.
Uses parameterized queries exclusively — never string interpolation in SQL.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from models import TelegramMessage

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      INTEGER NOT NULL,
    chat_id         INTEGER NOT NULL,
    chat_title      TEXT,
    sender_name     TEXT,
    text            TEXT    NOT NULL,
    date            TEXT    NOT NULL,
    reply_to_message_id INTEGER,
    content_hash    TEXT    NOT NULL,
    UNIQUE(chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_date ON messages (date);
CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages (chat_id);

CREATE TABLE IF NOT EXISTS sync_state (
    chat_id         INTEGER PRIMARY KEY,
    last_message_id INTEGER NOT NULL,
    last_sync       TEXT    NOT NULL
);
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    """Create/open the database, create tables, and return a connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def save_messages(conn: sqlite3.Connection, messages: list[TelegramMessage]) -> int:
    """Insert messages using INSERT OR IGNORE for idempotency.

    Returns the number of newly inserted rows.
    """
    if not messages:
        return 0

    cursor = conn.executemany(
        """INSERT OR IGNORE INTO messages
           (message_id, chat_id, chat_title, sender_name,
            text, date, reply_to_message_id, content_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                m.message_id,
                m.chat_id,
                m.chat_title,
                m.sender_name,
                m.text,
                m.date.isoformat(),
                m.reply_to_message_id,
                m.content_hash,
            )
            for m in messages
        ],
    )
    conn.commit()
    return cursor.rowcount


def _row_to_message(row: sqlite3.Row) -> TelegramMessage:
    """Convert a database Row to a frozen TelegramMessage."""
    return TelegramMessage(
        message_id=row["message_id"],
        chat_id=row["chat_id"],
        chat_title=row["chat_title"] or "",
        sender_name=row["sender_name"],
        text=row["text"],
        date=datetime.fromisoformat(row["date"]),
        reply_to_message_id=row["reply_to_message_id"],
        content_hash=row["content_hash"],
    )


def fetch_messages_since(
    conn: sqlite3.Connection, since: datetime
) -> list[TelegramMessage]:
    """Return all messages with date >= since, ordered by date ascending."""
    cursor = conn.execute(
        "SELECT * FROM messages WHERE date >= ? ORDER BY date ASC",
        (since.isoformat(),),
    )
    return [_row_to_message(row) for row in cursor.fetchall()]


def fetch_messages_by_chat(
    conn: sqlite3.Connection, chat_id: int, since: datetime
) -> list[TelegramMessage]:
    """Return messages for a specific chat since a given datetime."""
    cursor = conn.execute(
        "SELECT * FROM messages WHERE chat_id = ? AND date >= ? ORDER BY date ASC",
        (chat_id, since.isoformat()),
    )
    return [_row_to_message(row) for row in cursor.fetchall()]


def get_last_message_id(conn: sqlite3.Connection, chat_id: int) -> int | None:
    """Return the last synced message_id for a chat, or None if never synced."""
    cursor = conn.execute(
        "SELECT last_message_id FROM sync_state WHERE chat_id = ?",
        (chat_id,),
    )
    row = cursor.fetchone()
    return row["last_message_id"] if row else None


def update_sync_state(
    conn: sqlite3.Connection, chat_id: int, last_message_id: int
) -> None:
    """Upsert the sync cursor for a chat."""
    conn.execute(
        """INSERT INTO sync_state (chat_id, last_message_id, last_sync)
           VALUES (?, ?, ?)
           ON CONFLICT(chat_id) DO UPDATE SET
               last_message_id = excluded.last_message_id,
               last_sync = excluded.last_sync""",
        (chat_id, last_message_id, datetime.now(tz=UTC).isoformat()),
    )
    conn.commit()


def message_count(conn: sqlite3.Connection) -> int:
    """Return the total number of stored messages."""
    cursor = conn.execute("SELECT COUNT(*) AS cnt FROM messages")
    row = cursor.fetchone()
    return row["cnt"]
