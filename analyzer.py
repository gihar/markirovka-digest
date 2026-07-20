"""Digest analyzer: format messages and generate the digest via an LLM.

Talks to any OpenAI-compatible chat-completions endpoint (OpenRouter or similar)
over raw httpx — base URL, API key, and model are configuration. Fail-loud: a
failed request or an unexpected/empty response raises rather than silently
producing no digest.
"""

import logging
from datetime import datetime
from itertools import groupby
from pathlib import Path
from typing import Callable

import httpx

from models import DigestResult, TelegramMessage
from window import MSK

logger = logging.getLogger(__name__)

# Cap on the generated digest length (tokens). Not a tuning knob worth exposing.
# Sized so the multi-part Telegram path has real headroom: hitting this cap now
# fails the run (truncation guard) instead of publishing a cut-off digest.
MAX_OUTPUT_TOKENS: int = 8192
_HTTP_TIMEOUT: int = 60

# finish_reason values that mean the provider stopped at the token cap, so the
# content is cut off mid-thought. Providers vary: OpenAI uses "length", some
# others report "max_tokens".
_TRUNCATION_FINISH_REASONS: frozenset[str] = frozenset({"length", "max_tokens"})


class LlmError(RuntimeError):
    """Raised when the LLM request fails or returns an unusable response."""


def _load_prompt(prompt_path: Path) -> str:
    """Read the system prompt from the prompts directory."""
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()


def _format_message(msg: TelegramMessage) -> str:
    """Format a single message as a markdown line, timestamped in Moscow time."""
    time_str = msg.date.astimezone(MSK).strftime("%H:%M")
    sender = msg.sender_name or "Unknown"
    return f"[{time_str}] **{sender}**: {msg.text}"


def prepare_messages_markdown(messages: list[TelegramMessage]) -> str:
    """Group messages by chat and format as markdown for the LLM.

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


def _http_post(url: str, headers: dict, payload: dict) -> dict:
    """POST to the chat-completions endpoint and return the parsed JSON.

    Raises LlmError on timeout or a non-2xx response.
    """
    try:
        with httpx.Client() as client:
            resp = client.post(
                url, headers=headers, json=payload, timeout=_HTTP_TIMEOUT
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        raise LlmError(f"LLM request failed: {exc}") from exc


def _extract_content(data: dict) -> str:
    """Pull the assistant message text out of a chat-completions response.

    Rejects responses the provider truncated at the token cap (finish_reason
    "length"/"max_tokens") so a digest cut off mid-thought never gets published.
    A missing or otherwise-valued finish_reason is accepted — many
    OpenAI-compatible providers omit or vary the field.
    """
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmError(f"Unexpected LLM response shape: {data!r}") from exc
    finish_reason = choice.get("finish_reason")
    if finish_reason in _TRUNCATION_FINISH_REASONS:
        raise LlmError(
            f"LLM digest truncated by the token limit (finish_reason={finish_reason!r})"
        )
    if not content or not content.strip():
        raise LlmError("LLM returned empty content")
    return content


def generate_digest(
    messages: list[TelegramMessage],
    prompt_path: Path,
    date: str | None = None,
    *,
    base_url: str,
    api_key: str,
    model: str,
    post: Callable[[str, dict, dict], dict] = _http_post,
) -> DigestResult:
    """Generate a digest from messages via an OpenAI-compatible LLM.

    Args:
        messages: Messages to analyze.
        prompt_path: Path to the system prompt markdown file.
        date: Digest date (YYYY-MM-DD) — the covered MSK day. Defaults to today.
        base_url: LLM base URL up to /v1 (chat/completions is appended).
        api_key: Bearer token.
        model: Provider model id (e.g. "anthropic/claude-sonnet-4.6").
        post: Injectable transport seam (url, headers, payload) -> response dict.

    Raises:
        FileNotFoundError: If the prompt file doesn't exist.
        LlmError: On request failure or an unusable response.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    prompt_text = _load_prompt(prompt_path)
    messages_md = prepare_messages_markdown(messages)

    if not messages_md:
        return DigestResult(
            date=date,
            markdown="Нет сообщений для дайджеста.",
            message_count=0,
            chat_count=0,
            token_count=0,
        )

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": [
            {"role": "system", "content": prompt_text},
            {"role": "user", "content": messages_md},
        ],
    }

    logger.info("Requesting digest: model=%s, ~%d input chars", model, len(messages_md))
    data = post(url, headers, payload)
    digest_markdown = _extract_content(data)

    chat_titles = {m.chat_title for m in messages}
    logger.info("Digest generated: %d chars, model=%s", len(digest_markdown), model)

    return DigestResult(
        date=date,
        markdown=digest_markdown,
        message_count=len(messages),
        chat_count=len(chat_titles),
        # Rough character-based estimate — no portable token counter across
        # arbitrary OpenAI-compatible providers.
        token_count=len(messages_md) // 4,
    )
