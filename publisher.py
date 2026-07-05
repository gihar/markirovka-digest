"""Publish the Digest to Telegram.

Telegram-only: GitHub Issues were dropped when the Digest Service moved off
GitHub Actions (the digest channel is its own archive). Long digests are split
into several messages rather than truncated. Synchronous — the pipeline no
longer uses asyncio.
"""

import html
import logging
import re
from typing import Callable

import httpx

from config import Config
from models import DigestResult

logger = logging.getLogger(__name__)

# Telegram's hard limit for a single message.
_TELEGRAM_LIMIT: int = 4096
_HTTP_TIMEOUT: int = 30


class TelegramDeliveryError(RuntimeError):
    """Raised when one or more Telegram message parts failed to send."""


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


def _largest_prefix(line: str, limit: int, measure: Callable[[str], int]) -> int:
    """Return the largest k>=1 such that ``measure(line[:k]) <= limit``.

    Binary search. Always returns at least 1 so hard-splitting makes progress
    even if a single unit already exceeds the limit.
    """
    lo, hi, best = 1, len(line), 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if measure(line[:mid]) <= limit:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def split_message(
    text: str,
    limit: int = _TELEGRAM_LIMIT,
    *,
    measure: Callable[[str], int] = len,
) -> list[str]:
    """Split text into parts each with ``measure(part) <= limit``, losslessly.

    Splits on line boundaries; a single line still too long is hard-split.
    ``measure`` lets callers size by the *rendered* length (e.g. HTML) while
    splitting the raw source, so a split never lands inside a produced tag or
    entity. No non-newline character is dropped or duplicated; order is kept.
    """
    if measure(text) <= limit:
        return [text]

    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        # A single line that is itself too long gets hard-split.
        while measure(line) > limit:
            if current:
                parts.append(current)
                current = ""
            k = _largest_prefix(line, limit, measure)
            parts.append(line[:k])
            line = line[k:]

        candidate = line if not current else f"{current}\n{line}"
        if measure(candidate) <= limit:
            current = candidate
        else:
            if current:
                parts.append(current)
            current = line

    if current:
        parts.append(current)
    return parts


def render_parts(digest: DigestResult, limit: int = _TELEGRAM_LIMIT) -> list[str]:
    """Render a Digest to ready-to-send Telegram HTML message parts.

    Splits the raw markdown — sizing each chunk by its rendered HTML length —
    then converts each chunk to HTML independently. This guarantees a split
    never severs an HTML tag or entity, so every part is valid under
    parse_mode=HTML. (In the rare case an oversized single line is hard-split
    mid-``**bold**``, the orphaned markers render as literal asterisks rather
    than producing malformed HTML — content is preserved either way.)
    """
    def html_len(chunk: str) -> int:
        return len(markdown_to_telegram_html(chunk))

    raw_parts = split_message(digest.markdown, limit, measure=html_len)
    return [markdown_to_telegram_html(part) for part in raw_parts]


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

    A failed part is logged but does not stop the remaining parts. If any part
    failed, raises TelegramDeliveryError after attempting them all — so a cron
    run that could not deliver the digest exits non-zero instead of looking
    successful.
    """
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    sent = 0
    failed = 0
    for part in parts:
        payload = {"chat_id": chat_id, "text": part, "parse_mode": "HTML"}
        try:
            post(url, payload)
            sent += 1
        except Exception as exc:  # network / API error — keep going
            failed += 1
            logger.error("Failed to send Telegram message part: %s", exc)

    if failed:
        raise TelegramDeliveryError(
            f"{failed} of {len(parts)} Telegram message part(s) failed to send"
        )
    return sent


def publish(digest: DigestResult, config: Config) -> int:
    """Publish the Digest to the Telegram digest channel. Returns parts sent."""
    parts = render_parts(digest)
    return send_parts(
        parts, config.telegram_bot_token, config.telegram_digest_chat_id
    )
