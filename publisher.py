"""Publish the Digest to Telegram.

Telegram-only: GitHub Issues were dropped when the Digest Service moved off
GitHub Actions (the digest channel is its own archive). Long digests are split
into several messages rather than truncated. Synchronous — the pipeline no
longer uses asyncio.
"""

import html
import logging
import re
import time
from datetime import date
from typing import Callable

import httpx

from config import Config
from models import DigestResult

logger = logging.getLogger(__name__)

# Telegram's hard limit for a single message.
_TELEGRAM_LIMIT: int = 4096
_HTTP_TIMEOUT: int = 30

# Prefix marking every part after the first as a continuation of the digest,
# so readers of a split digest know parts 2+ are not a fresh message. Rendered
# to <i>(продолжение)</i> followed by a blank line.
_CONTINUATION_MARKER: str = "*(продолжение)*\n\n"

# Pace multi-part sends: Telegram's per-chat flood limit is ~1 message/second.
_TELEGRAM_SEND_INTERVAL: float = 1.0
# Bounded retries when Telegram asks us to wait (HTTP 429 + retry_after).
_MAX_FLOOD_RETRIES: int = 2
# Fallback wait if a 429 carries no retry_after we can read.
_DEFAULT_RETRY_AFTER: float = 3.0


class TelegramDeliveryError(RuntimeError):
    """Raised when one or more Telegram message parts failed to send."""


class TelegramFloodError(Exception):
    """Raised on HTTP 429 — Telegram asked us to wait ``retry_after`` seconds."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"Telegram flood wait: {retry_after}s")


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
    # LLMs habitually backslash-escape markdown punctuation (tasy\_emdina).
    # Telegram HTML renders the backslash literally, so strip escapes of
    # characters this converter never treats as markup ('*' stays significant).
    text = re.sub(r"\\([_\[\]()~`#+\-=|{}.!])", r"\1", text)
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


def _dated_header(date_str: str) -> str:
    """A bold, human-friendly digest title from an ISO date (→ DD.MM.YYYY).

    Injected by us — the single title of the message — rather than trusting the
    model to state the covered day (the prompt forbids the model its own title).
    """
    day = date.fromisoformat(date_str)
    return f"🗓 **Дайджест чатов по маркировке за {day.strftime('%d.%m.%Y')}**"


# A whole line that is only a Markdown horizontal rule (---, ***, ___, - - -).
_HR_LINE = re.compile(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$", re.MULTILINE)


def _strip_horizontal_rules(md: str) -> str:
    """Drop horizontal-rule lines; Telegram shows them as literal '---' clutter.

    Sections stay separated by the surrounding blank lines (runs collapsed).
    """
    md = _HR_LINE.sub("", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def render_parts(digest: DigestResult, limit: int = _TELEGRAM_LIMIT) -> list[str]:
    """Render a Digest to ready-to-send Telegram HTML message parts.

    Prepends a dated header (which day this digest covers) to the body, strips
    horizontal-rule lines, then splits the raw markdown — sizing each chunk by
    its rendered HTML length — and converts each chunk to HTML independently.
    The header lands on the first part only. Splitting on the raw source
    guarantees a split never severs an HTML tag or entity, so every part is
    valid under parse_mode=HTML. (In the rare case an oversized single line is
    hard-split mid-``**bold**``, the orphaned markers render as literal
    asterisks — content is preserved either way.)

    When the digest spans more than one message, every part after the first is
    prefixed with a continuation marker. The marker's rendered length is
    reserved during splitting (parts are sized to ``limit`` minus the marker),
    so a part plus its marker never exceeds the limit. A digest that fits in a
    single message gets no marker and is unchanged.
    """
    def html_len(chunk: str) -> int:
        return len(markdown_to_telegram_html(chunk))

    body = f"{_dated_header(digest.date)}\n\n{_strip_horizontal_rules(digest.markdown)}"

    # A digest that fits whole stays a single, marker-free message.
    if html_len(body) <= limit:
        return [markdown_to_telegram_html(body)]

    # It splits: reserve room for the marker on every part but the first. The
    # marker sits behind an inert blank line, so its rendered length adds to a
    # chunk's rendered length exactly — reducing the limit by it is precise.
    marker_len = html_len(_CONTINUATION_MARKER)
    raw_parts = split_message(body, limit - marker_len, measure=html_len)
    return [
        markdown_to_telegram_html(part if i == 0 else _CONTINUATION_MARKER + part)
        for i, part in enumerate(raw_parts)
    ]


def _parse_retry_after(resp: httpx.Response) -> float:
    """Extract the flood-wait seconds from a 429 response."""
    try:
        retry_after = resp.json().get("parameters", {}).get("retry_after")
        if retry_after is not None:
            return float(retry_after)
    except (ValueError, AttributeError, TypeError):
        pass
    header = resp.headers.get("Retry-After")
    return float(header) if header else _DEFAULT_RETRY_AFTER


def _http_post(url: str, payload: dict) -> None:
    """Send one POST to Telegram.

    Raises TelegramFloodError on 429 (recoverable — wait and retry) and the
    underlying httpx error on any other non-2xx.
    """
    with httpx.Client() as client:
        resp = client.post(url, json=payload, timeout=_HTTP_TIMEOUT)
        if resp.status_code == 429:
            raise TelegramFloodError(_parse_retry_after(resp))
        resp.raise_for_status()


def _send_one(url, payload, post, sleep, max_flood_retries: int) -> bool:
    """Send one part, honoring Telegram flood-waits. Returns True on success."""
    for attempt in range(max_flood_retries + 1):
        try:
            post(url, payload)
            return True
        except TelegramFloodError as exc:
            if attempt >= max_flood_retries:
                logger.error("Flood limit not cleared after retries: %s", exc)
                return False
            logger.warning(
                "Telegram flood: waiting %.1fs before retry", exc.retry_after
            )
            sleep(exc.retry_after)
        except Exception as exc:  # network / API error — permanent for this part
            logger.error("Failed to send Telegram message part: %s", exc)
            return False
    return False


def send_parts(
    parts: list[str],
    token: str,
    chat_id: str,
    *,
    post=_http_post,
    sleep: Callable[[float], None] = time.sleep,
    max_flood_retries: int = _MAX_FLOOD_RETRIES,
    interval: float = _TELEGRAM_SEND_INTERVAL,
) -> int:
    """Send each part as a separate Telegram message. Returns parts sent.

    Paces multi-part sends by ``interval`` to stay under Telegram's per-chat
    flood limit, and honors a 429's retry_after with bounded retries. A part
    that still fails is logged and does not stop the rest; if any part failed,
    raises TelegramDeliveryError after attempting them all — so a cron run that
    could not deliver the digest exits non-zero instead of looking successful.
    """
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    sent = 0
    failed = 0
    for i, part in enumerate(parts):
        if i > 0:
            sleep(interval)  # pace to stay under the per-chat flood limit
        payload = {"chat_id": chat_id, "text": part, "parse_mode": "HTML"}
        if _send_one(url, payload, post, sleep, max_flood_retries):
            sent += 1
        else:
            failed += 1

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
