"""Publish digest to GitHub Issues and Telegram.

Delivers the generated digest to configured destinations with graceful
degradation: if one target fails, the other is still attempted.
"""

import html
import logging
import re

import httpx

from config import Config
from models import DigestResult

logger = logging.getLogger(__name__)

# Telegram message limit is 4096 chars; leave room for the link footer
_TELEGRAM_TRUNCATE_AT: int = 3700
_TELEGRAM_MAX_LENGTH: int = 3800
_HTTP_TIMEOUT: int = 30


def markdown_to_telegram_html(md: str) -> str:
    """Convert markdown text to Telegram-compatible HTML.

    Steps:
        1. Escape all HTML entities in raw text first.
        2. Convert markdown bold (**text**) to <b>text</b>.
        3. Convert markdown headers (## Header) to <b>Header</b>.
        4. Preserve line breaks.
    """
    # Step 1: escape HTML entities in the raw text
    text = html.escape(md)

    # Step 2: convert headers (## Header) — must come before bold
    # Match lines starting with one or more # followed by space and text
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # Step 3: convert bold **text** to <b>text</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

    # Step 4: convert italic *text* to <i>text</i> (single asterisk, not double)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)

    return text


async def publish_github_issue(digest: DigestResult, config: Config) -> str | None:
    """Create a GitHub issue with the digest content.

    Args:
        digest: The generated digest to publish.
        config: Application configuration with GitHub credentials.

    Returns:
        The HTML URL of the created issue, or None if skipped/failed.
    """
    if not config.github_token or not config.github_repository:
        logger.info("GitHub not configured, skipping issue creation")
        return None

    url = f"https://api.github.com/repos/{config.github_repository}/issues"
    headers = {
        "Authorization": f"Bearer {config.github_token}",
        "Accept": "application/vnd.github+json",
    }
    # Year-month label, e.g. "2026-03"
    label = digest.date[:7]
    payload = {
        "title": f"\u0414\u0430\u0439\u0434\u0436\u0435\u0441\u0442: {digest.date}",
        "body": digest.markdown,
        "labels": ["digest", label],
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url, json=payload, headers=headers, timeout=_HTTP_TIMEOUT
            )
            resp.raise_for_status()
            issue_url: str = resp.json()["html_url"]
            logger.info("GitHub issue created: %s", issue_url)
            return issue_url
    except httpx.HTTPError as exc:
        logger.error("Failed to create GitHub issue: %s", exc)
        return None


async def publish_telegram(
    digest: DigestResult,
    issue_url: str | None,
    config: Config,
) -> bool:
    """Send digest to a Telegram chat via Bot API.

    Args:
        digest: The generated digest to publish.
        issue_url: Optional link to the full GitHub issue.
        config: Application configuration with Telegram credentials.

    Returns:
        True if the message was sent successfully, False otherwise.
    """
    if not config.telegram_bot_token or not config.telegram_digest_chat_id:
        logger.info("Telegram not configured, skipping message")
        return False

    text = markdown_to_telegram_html(digest.markdown)

    if len(text) > _TELEGRAM_MAX_LENGTH:
        text = text[:_TELEGRAM_TRUNCATE_AT]
        if issue_url:
            text += f"\n\n<a href='{issue_url}'>\u041f\u043e\u043b\u043d\u044b\u0439 \u0434\u0430\u0439\u0434\u0436\u0435\u0441\u0442 \u2192</a>"
        else:
            text += "\n\n<i>[\u0422\u0435\u043a\u0441\u0442 \u0441\u043e\u043a\u0440\u0430\u0449\u0451\u043d]</i>"

    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": config.telegram_digest_chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=_HTTP_TIMEOUT)
            resp.raise_for_status()
            logger.info("Telegram message sent to %s", config.telegram_digest_chat_id)
            return True
    except httpx.HTTPError as exc:
        logger.error("Failed to send Telegram message: %s", exc)
        return False


async def publish(digest: DigestResult, config: Config) -> None:
    """Publish digest to all configured destinations.

    Attempts GitHub first (to get the issue URL for Telegram), then Telegram.
    Each target is independent: failure of one does not block the other.
    """
    issue_url = await publish_github_issue(digest, config)
    await publish_telegram(digest, issue_url, config)
