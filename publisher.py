"""Publish the Digest to Telegram.

Telegram-only: GitHub Issues were dropped when the Digest Service moved off
GitHub Actions (the digest channel is its own archive). Long digests are split
into several messages rather than truncated. Synchronous — the pipeline no
longer uses asyncio.
"""

import html
import logging
import re

import httpx

from config import Config
from models import DigestResult

logger = logging.getLogger(__name__)

# Telegram's hard limit for a single message.
_TELEGRAM_LIMIT: int = 4096
_HTTP_TIMEOUT: int = 30


def markdown_to_telegram_html(md: str) -> str:
    """Convert markdown to Telegram-compatible HTML.

    Escapes HTML entities first, then converts headers and bold/italic. Bold and
    italic stay within a single line, so line-boundary splitting never cuts a tag
    unless one line alone exceeds the Telegram limit.
    """
    text = html.escape(md)
    # Headers (## Header) must be handled before bold.
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    # Bold **text** before italic *text*.
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    return text


def split_message(text: str, limit: int = _TELEGRAM_LIMIT) -> list[str]:
    """Split text into parts of at most ``limit`` chars without losing content.

    Splits on line boundaries; a single line longer than ``limit`` is hard-split.
    No non-newline character is dropped or duplicated, and order is preserved.
    """
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        # A single line that is itself too long gets hard-split.
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]

        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
        else:
            parts.append(current)
            current = line

    if current:
        parts.append(current)
    return parts


def render_parts(digest: DigestResult, limit: int = _TELEGRAM_LIMIT) -> list[str]:
    """Render a Digest to ready-to-send Telegram HTML message parts."""
    return split_message(markdown_to_telegram_html(digest.markdown), limit)


def _http_post(url: str, payload: dict) -> None:
    """Send one POST to Telegram, raising on non-2xx."""
    with httpx.Client() as client:
        resp = client.post(url, json=payload, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()


def send_parts(
    parts: list[str],
    token: str,
    chat_id: str,
    *,
    post=_http_post,
) -> int:
    """Send each part as a separate Telegram message. Returns parts sent.

    A failed part is logged and does not stop the remaining parts.
    """
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    sent = 0
    for part in parts:
        payload = {"chat_id": chat_id, "text": part, "parse_mode": "HTML"}
        try:
            post(url, payload)
            sent += 1
        except Exception as exc:  # network / API error — keep going
            logger.error("Failed to send Telegram message part: %s", exc)
    return sent


def publish(digest: DigestResult, config: Config) -> int:
    """Publish the Digest to the Telegram digest channel. Returns parts sent."""
    parts = render_parts(digest)
    return send_parts(
        parts, config.telegram_bot_token, config.telegram_digest_chat_id
    )
