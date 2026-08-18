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
class LlmProvider:
    """An OpenAI-compatible endpoint the Digest can be generated through.

    The three values only mean anything together — a base URL without its key
    and model names nothing callable — so they travel as one value. A fallback
    provider (ADR-0002) is then another value of this type rather than another
    triple of parallel arguments.
    """

    base_url: str  # up to /v1; "/chat/completions" is appended
    api_key: str
    model: str  # provider model id, e.g. "anthropic/claude-sonnet-4.6"


@dataclass(frozen=True)
class DigestResult:
    """The output of the digest generation pipeline."""

    date: str  # YYYY-MM-DD — the covered Europe/Moscow calendar day
    markdown: str
    message_count: int
    chat_count: int
    token_count: int
    # Model that actually produced this digest — the primary one on a normal
    # day, the fallback's when the primary was down (ADR-0002). None when no
    # LLM ran at all, i.e. there was nothing to digest.
    model: str | None
