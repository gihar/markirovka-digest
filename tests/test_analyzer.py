"""Behavior: format messages, then generate the digest via an OpenAI-compatible LLM."""

from datetime import UTC, datetime

import pytest

from analyzer import LlmError, generate_digest, prepare_messages_markdown
from models import TelegramMessage


def _msg(chat="Маркировка. Молоко", text="привет"):
    return TelegramMessage(-1, chat, "ivan", text, datetime(2026, 7, 4, 10, 0, tzinfo=UTC))


def _prompt(tmp_path):
    p = tmp_path / "digest.md"
    p.write_text("Ты аналитик маркировки.", encoding="utf-8")
    return p


def _ok_response(content="# Дайджест\nитоги дня"):
    return {"choices": [{"message": {"content": content}}]}


def test_groups_by_chat_and_shows_moscow_time():
    messages = [
        TelegramMessage(-1, "Маркировка. Молоко", "ivan", "привет",
                        datetime(2026, 7, 4, 10, 0, tzinfo=UTC)),   # 13:00 MSK
        TelegramMessage(-2, "Маркировка. Главный", "petr", "вопрос",
                        datetime(2026, 7, 4, 7, 30, tzinfo=UTC)),    # 10:30 MSK
    ]

    md = prepare_messages_markdown(messages)

    assert "## Маркировка. Главный" in md
    assert "## Маркировка. Молоко" in md
    assert "[13:00] **ivan**: привет" in md
    assert "[10:30] **petr**: вопрос" in md


def test_generate_digest_posts_openai_payload_and_returns_content(tmp_path):
    captured = {}

    def fake_post(url, headers, payload):
        captured.update(url=url, headers=headers, payload=payload)
        return _ok_response("# Дайджест\nитоги дня")

    result = generate_digest(
        [_msg()], _prompt(tmp_path), "2026-07-04",
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-or-xxx",
        model="anthropic/claude-sonnet-4.6",
        post=fake_post,
    )

    assert result.markdown == "# Дайджест\nитоги дня"
    assert result.date == "2026-07-04"
    assert result.message_count == 1

    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-or-xxx"
    payload = captured["payload"]
    assert payload["model"] == "anthropic/claude-sonnet-4.6"
    assert payload["messages"][0] == {"role": "system", "content": "Ты аналитик маркировки."}
    assert payload["messages"][1]["role"] == "user"
    assert "Маркировка. Молоко" in payload["messages"][1]["content"]


def _generate(tmp_path, post):
    return generate_digest(
        [_msg()], _prompt(tmp_path), "2026-07-04",
        base_url="https://x/v1", api_key="k", model="m", post=post,
    )


def test_malformed_response_raises(tmp_path):
    with pytest.raises(LlmError):
        _generate(tmp_path, post=lambda url, headers, payload: {"error": "boom"})


def test_empty_content_raises(tmp_path):
    with pytest.raises(LlmError):
        _generate(
            tmp_path,
            post=lambda url, headers, payload: _ok_response(content="   "),
        )


def test_transport_failure_propagates(tmp_path):
    def boom(url, headers, payload):
        raise LlmError("network down")

    with pytest.raises(LlmError):
        _generate(tmp_path, post=boom)
