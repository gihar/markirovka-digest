"""Gold standard: SQLite operations with parameterized queries.

Rules:
- All functions take a Connection as the first argument (no global state).
- Use parameterized queries exclusively (? placeholders, never f-strings).
- Use INSERT OR IGNORE for idempotent writes.
- Return frozen dataclasses from queries, not raw Rows.
Extracted from db.py.
"""

import sqlite3
from datetime import datetime

from models import TelegramMessage


def save_messages(conn: sqlite3.Connection, messages: list[TelegramMessage]) -> int:
    """Insert messages using INSERT OR IGNORE for idempotency."""
    if not messages:
        return 0

    cursor = conn.executemany(
        """INSERT OR IGNORE INTO messages
           (message_id, chat_id, chat_title, sender_name,
            text, date, reply_to_message_id, content_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [(m.message_id, m.chat_id, m.chat_title, m.sender_name,
          m.text, m.date.isoformat(), m.reply_to_message_id, m.content_hash)
         for m in messages],
    )
    conn.commit()
    return cursor.rowcount
