"""Telegram channel downloader using Telethon.

Downloads recent messages from configured channels, converts them to
frozen TelegramMessage dataclasses, and persists via the db module.
Uses StringSession from config (never from file).
Sequential channel download with circuit breaker and rate limiting.
"""

import asyncio
import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.errors import ChannelPrivateError, ChatAdminRequiredError
from telethon.sessions import StringSession

from config import Config
from db import save_messages, update_sync_state
from models import ChannelConfig, TelegramMessage

# Circuit breaker threshold: skip channel after this many consecutive failures
_MAX_FAILURES: int = 3

# Delay between channel downloads to avoid flood limits (seconds)
_INTER_CHANNEL_DELAY: float = 2.0


def _compute_content_hash(text: str) -> str:
    """Return the SHA-256 hex digest of a message's text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_sender_name(message: object) -> str | None:
    """Extract a human-readable sender name from a Telethon message."""
    sender = message.sender  # type: ignore[attr-defined]
    if sender is None:
        return None
    first = getattr(sender, "first_name", None) or ""
    last = getattr(sender, "last_name", None) or ""
    full = f"{first} {last}".strip()
    if full:
        return full
    username = getattr(sender, "username", None)
    if username:
        return f"@{username}"
    title = getattr(sender, "title", None)
    return title


def _should_skip_message(message: object, min_length: int) -> bool:
    """Return True if the message should be excluded from the digest."""
    # Skip service messages (joins, leaves, pin, etc.)
    if getattr(message, "action", None) is not None:
        return True
    # Skip messages without text
    text = getattr(message, "text", None) or getattr(message, "message", None)
    if not text:
        return True
    # Skip messages shorter than the configured minimum
    if len(text) < min_length:
        return True
    return False


def _to_telegram_message(
    message: object,
    channel: ChannelConfig,
) -> TelegramMessage:
    """Convert a Telethon message object to a frozen TelegramMessage."""
    text: str = getattr(message, "text", None) or getattr(message, "message", "")
    reply_to = getattr(message, "reply_to_msg_id", None)
    msg_date: datetime = message.date  # type: ignore[attr-defined]

    return TelegramMessage(
        message_id=message.id,  # type: ignore[attr-defined]
        chat_id=channel.chat_id,
        chat_title=channel.title,
        sender_name=_extract_sender_name(message),
        text=text,
        date=msg_date.replace(tzinfo=timezone.utc) if msg_date.tzinfo is None else msg_date,
        reply_to_message_id=reply_to,
        content_hash=_compute_content_hash(text),
    )


async def _download_channel(
    client: TelegramClient,
    channel: ChannelConfig,
    config: Config,
    conn: sqlite3.Connection,
) -> int:
    """Download messages from a single channel.

    Returns the number of newly saved messages.
    Implements a circuit breaker: if 3 consecutive processing failures
    occur, the channel is skipped.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=config.digest_hours)
    batch: list[TelegramMessage] = []
    failures: int = 0
    max_message_id: int = 0

    async for message in client.iter_messages(channel.chat_id, offset_date=cutoff):
        # Stop if we've gone past the time window
        msg_date = message.date
        if msg_date.tzinfo is None:
            msg_date = msg_date.replace(tzinfo=timezone.utc)
        if msg_date < cutoff:
            break

        # Circuit breaker
        if failures >= _MAX_FAILURES:
            print(f"Circuit breaker: skipping {channel.title}")
            break

        try:
            if _should_skip_message(message, config.min_message_length):
                continue

            telegram_msg = _to_telegram_message(message, channel)
            batch.append(telegram_msg)

            if message.id > max_message_id:
                max_message_id = message.id

        except Exception as exc:
            failures += 1
            print(f"Error processing message {message.id} in {channel.title}: {exc}")

    saved = save_messages(conn, batch)

    if max_message_id > 0:
        update_sync_state(conn, channel.chat_id, max_message_id)

    print(f"  {channel.title}: fetched {len(batch)}, saved {saved} new")
    return saved


async def download_messages(config: Config, conn: sqlite3.Connection) -> int:
    """Download messages from all configured channels.

    Uses StringSession from config. Downloads channels sequentially
    with a 2-second delay between each to respect Telegram rate limits.

    Returns the total number of newly saved messages.
    """
    client = TelegramClient(
        StringSession(config.telegram_session),
        config.telegram_api_id,
        config.telegram_api_hash,
    )
    client.flood_sleep_threshold = 60

    async with client:
        total: int = 0
        for channel in config.channels:
            try:
                count = await _download_channel(client, channel, config, conn)
                total += count
            except (ChatAdminRequiredError, ChannelPrivateError) as exc:
                print(f"Skipping {channel.title}: {exc}")
            except Exception as exc:
                print(f"Unexpected error downloading {channel.title}: {exc}")
            await asyncio.sleep(_INTER_CHANNEL_DELAY)
        return total
