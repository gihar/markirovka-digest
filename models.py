"""Frozen dataclasses for all domain models."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TelegramMessage:
    """A message read from the Message Store for inclusion in a Digest.

    Only the fields the Digest actually uses. The Digest Service is a read-only
    consumer (see ADR-0001); it never persists these back.
    """

    chat_id: int
    chat_title: str
    sender_name: str
    text: str
    date: datetime


@dataclass(frozen=True)
class ChannelConfig:
    """A Monitored Chat in the digest allow-list.

    Only the chat id is configured; the human-readable title lives in the
    Message Store (chats.title).
    """

    chat_id: int


@dataclass(frozen=True)
class DigestResult:
    """The output of the digest generation pipeline."""

    date: str  # YYYY-MM-DD — the covered Europe/Moscow calendar day
    markdown: str
    message_count: int
    chat_count: int
    token_count: int
