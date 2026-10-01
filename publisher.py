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
from render import TELEGRAM_LIMIT, render_parts

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT: int = 30

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
        payload = {
            "chat_id": chat_id,
            "text": part,
            "parse_mode": "HTML",
            # A digest links every theme to its discussion; without this,
            # Telegram attaches a preview card of the first link to each part.
            "link_preview_options": {"is_disabled": True},
        }
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


# Marks an alert whose error message was too long to carry whole.
_CLIP_MARK: str = "…"
# A half-written entity ("&am") left by a clip would break the HTML parse.
_TRAILING_ENTITY = re.compile(r"&[#0-9a-zA-Z]*$")


def _clip_html(text: str, limit: int) -> str:
    """Clip already-escaped HTML to ``limit`` chars, never inside an entity."""
    if len(text) <= limit:
        return text
    cut = _TRAILING_ENTITY.sub("", text[: limit - len(_CLIP_MARK)])
    return cut + _CLIP_MARK


def _default_subject(day: date) -> str:
    return f"Дайджест за {day.strftime('%d.%m.%Y')}"


def render_alert(
    day: date, error: BaseException, *, subject: str | None = None
) -> str:
    """Render a failed run as one short Telegram message.

    Names what was not published (``subject``; the Digest for ``day`` unless
    given) and the exception, and stays within a single message:
    an alert that had to be split, or that overran the limit and was rejected,
    is an alert that does not arrive.
    """
    header = f"⚠️ <b>{subject or _default_subject(day)} не опубликован</b>\n\n"
    reason = str(error) or error.__class__.__name__
    # Everything below the header comes from an exception, so it is
    # accident-shaped text: an LlmError carries the provider's response body,
    # which can be a whole HTML page from a proxy. Escaped, or Telegram rejects
    # the alert with a 400 and the failure stays invisible. Escaping can inflate
    # the text fivefold ('&' -> '&amp;'), so the clip is applied after it.
    # quote=False: Telegram needs only <, > and & escaped in text, and provider
    # error bodies are quote-heavy JSON — escaping quotes would spend the
    # message budget without changing a single character the reader sees.
    body = html.escape(f"{error.__class__.__name__}: {reason}", quote=False)
    return header + _clip_html(body, TELEGRAM_LIMIT - len(header))


def alert_failure(
    day: date,
    error: BaseException,
    config: Config,
    *,
    subject: str | None = None,
    post=_http_post,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Tell the alert chat that the run for ``day`` failed. Never raises.

    Best-effort by contract: an unconfigured alert chat and an undeliverable
    alert are both logged and swallowed, so callers can alert and then re-raise
    the original failure without the alert ever becoming the failure.
    """
    if not config.telegram_alert_chat_id:
        logger.warning(
            "Run for %s failed and TELEGRAM_ALERT_CHAT_ID is not set — "
            "no alert sent. The failure is in this log only.",
            day,
        )
        return

    try:
        send_parts(
            [render_alert(day, error, subject=subject)],
            config.telegram_bot_token,
            config.telegram_alert_chat_id,
            post=post,
            sleep=sleep,
        )
    except Exception as alert_error:
        # Deliberately broad: the caller re-raises the failure this alert
        # reports, and an alert that raised would replace the reason the digest
        # died with the reason Telegram was unhappy.
        logger.error(
            "Could not deliver the failure alert for %s: %s", day, alert_error
        )


def render_degraded_alert(
    day: date, digest: DigestResult, *, subject: str | None = None
) -> str:
    """Render a fallback-served run as one short Telegram message.

    Deliberately unlike the failure alert: that one means no digest came out at
    all, this one means the digest is published but thinner than usual, and the
    two call for different reactions.
    """
    header = (
        f"🟡 <b>{subject or _default_subject(day)} "
        f"опубликован на запасном провайдере</b>\n\n"
    )
    body = f"Модель: {digest.model}\n"
    if digest.provider_failures:
        body += "Основной провайдер не ответил:\n" + "\n".join(
            digest.provider_failures
        )
    # Same escaping and clipping as the failure alert: the reasons carry the
    # provider's own response body, which can be an HTML page from a proxy.
    escaped = html.escape(body, quote=False)
    return header + _clip_html(escaped, TELEGRAM_LIMIT - len(header))


def alert_degraded(
    day: date,
    digest: DigestResult,
    config: Config,
    *,
    subject: str | None = None,
    post=_http_post,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Tell the alert chat that a non-primary provider produced the digest.

    No-op when the primary produced the digest, which is every normal day, and
    when no LLM ran at all (model is None) — a day with nothing to digest is
    quiet, not degraded.

    Never raises: the digest is already published and the run is a success, so
    an undeliverable alert must not turn it into a failure.
    """
    if digest.model is None or digest.model == config.primary_model:
        return

    if not config.telegram_alert_chat_id:
        logger.warning(
            "Digest for %s came from the fallback model %s and "
            "TELEGRAM_ALERT_CHAT_ID is not set — no alert sent. Skipped: %s",
            day,
            digest.model,
            "; ".join(digest.provider_failures) or "unknown",
        )
        return

    try:
        send_parts(
            [render_degraded_alert(day, digest, subject=subject)],
            config.telegram_bot_token,
            config.telegram_alert_chat_id,
            post=post,
            sleep=sleep,
        )
    except Exception as alert_error:
        # Deliberately broad, and for the opposite reason to alert_failure: the
        # digest for this day is already out, so a run that succeeded must not
        # start failing because Telegram would not take the footnote about it.
        logger.error(
            "Could not deliver the degraded-run alert for %s: %s", day, alert_error
        )
