"""Frozen dataclasses for all domain models."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TelegramMessage:
    """A single message downloaded from a Telegram chat."""

    message_id: int
    chat_id: int
    chat_title: str
    sender_name: str | None
    text: str
    date: datetime
    reply_to_message_id: int | None
    content_hash: str  # SHA-256 of text


@dataclass(frozen=True)
class ChannelConfig:
    """A Telegram channel to monitor, loaded from channels.toml."""

    chat_id: int
    title: str


@dataclass(frozen=True)
class DigestResult:
    """The output of the digest generation pipeline."""

    date: str  # YYYY-MM-DD
    markdown: str
    message_count: int
    chat_count: int
    token_count: int
