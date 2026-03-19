"""Digest analyzer: prepares messages and generates digest via Claude.

Groups Telegram messages by chat, formats them as markdown, counts tokens
using Anthropic's native counter, and sends to Claude for analysis.
"""

import logging
from datetime import datetime
from itertools import groupby
from pathlib import Path

import anthropic

from config import MAX_CONTEXT_TOKENS
from models import DigestResult, TelegramMessage

logger = logging.getLogger(__name__)

MODEL: str = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS: int = 4096


def _load_prompt(prompt_path: Path) -> str:
    """Read the system prompt from the prompts directory."""
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()


def _format_message(msg: TelegramMessage) -> str:
    """Format a single message as a markdown line."""
    time_str = msg.date.strftime("%H:%M")
    sender = msg.sender_name or "Unknown"
    return f"[{time_str}] **{sender}**: {msg.text}"


def prepare_messages_markdown(
    messages: list[TelegramMessage],
) -> str:
    """Group messages by chat and format as markdown for Claude.

    Returns a markdown string with chat headers and timestamped messages.
    """
    if not messages:
        return ""

    sorted_msgs = sorted(messages, key=lambda m: (m.chat_title, m.date))

    sections: list[str] = []
    for chat_title, chat_messages in groupby(sorted_msgs, key=lambda m: m.chat_title):
        lines = [f"## {chat_title}", ""]
        for msg in chat_messages:
            lines.append(_format_message(msg))
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def _count_tokens(client: anthropic.Anthropic, text: str) -> int:
    """Count tokens using Anthropic's native token counter."""
    result = client.messages.count_tokens(
        model=MODEL,
        messages=[{"role": "user", "content": text}],
    )
    return result.input_tokens


def _truncate_to_fit(
    messages_md: str, client: anthropic.Anthropic
) -> str:
    """Truncate message markdown to fit within MAX_CONTEXT_TOKENS.

    Uses a simple binary-search-like approach: cut text in half until it fits.
    """
    lines = messages_md.split("\n")
    # Remove lines from the end until we fit
    low, high = 0, len(lines)
    while low < high:
        mid = (low + high + 1) // 2
        candidate = "\n".join(lines[:mid])
        count = _count_tokens(client, candidate)
        if count <= MAX_CONTEXT_TOKENS:
            low = mid
        else:
            high = mid - 1

    return "\n".join(lines[:low])


def generate_digest(
    messages: list[TelegramMessage],
    prompt_path: Path,
    date: str | None = None,
) -> DigestResult:
    """Generate a digest from messages using Claude.

    Args:
        messages: List of Telegram messages to analyze.
        prompt_path: Path to the system prompt markdown file.
        date: Digest date in YYYY-MM-DD format. Defaults to today.

    Returns:
        Frozen DigestResult with the generated markdown digest.

    Raises:
        FileNotFoundError: If prompt file doesn't exist.
        anthropic.APIError: On API failures.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    prompt_text = _load_prompt(prompt_path)
    messages_md = prepare_messages_markdown(messages)

    if not messages_md:
        return DigestResult(
            date=date,
            markdown="No messages to analyze.",
            message_count=0,
            chat_count=0,
            token_count=0,
        )

    client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY env var

    token_count = _count_tokens(client, messages_md)
    logger.info("Token count: %d (model: %s)", token_count, MODEL)

    if token_count > MAX_CONTEXT_TOKENS:
        logger.warning(
            "Token count %d exceeds limit %d, truncating",
            token_count,
            MAX_CONTEXT_TOKENS,
        )
        messages_md = _truncate_to_fit(messages_md, client)
        token_count = _count_tokens(client, messages_md)
        logger.info("Truncated token count: %d", token_count)

    chat_titles = {m.chat_title for m in messages}

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=prompt_text,
        messages=[{"role": "user", "content": messages_md}],
    )

    digest_markdown = response.content[0].text
    logger.info("Digest generated: %d chars, model: %s", len(digest_markdown), MODEL)

    return DigestResult(
        date=date,
        markdown=digest_markdown,
        message_count=len(messages),
        chat_count=len(chat_titles),
        token_count=token_count,
    )
