"""Behavior: format messages, then generate the digest via an OpenAI-compatible LLM."""

from datetime import UTC, datetime

import httpx
import pytest

from analyzer import LlmError, _http_post, generate_digest, prepare_messages_markdown
from models import TelegramMessage


def _msg(chat="Маркировка. Молоко", text="привет"):
    return TelegramMessage(-1, chat, "ivan", text, datetime(2026, 7, 4, 10, 0, tzinfo=UTC))


def _prompt(tmp_path):
    p = tmp_path / "digest.md"
    p.write_text("Ты аналитик маркировки.", encoding="utf-8")
    return p


def _ok_response(content="# Дайджест\nитоги дня"):
    return {"choices": [{"message": {"content": content}}]}


def _response_with_finish(finish_reason, content="# Дайджест\nитоги дня"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}


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


def test_truncated_finish_reason_length_raises(tmp_path):
    with pytest.raises(LlmError, match="length"):
        _generate(
            tmp_path,
            post=lambda url, headers, payload: _response_with_finish("length"),
        )


def test_truncated_finish_reason_max_tokens_raises(tmp_path):
    with pytest.raises(LlmError, match="max_tokens"):
        _generate(
            tmp_path,
            post=lambda url, headers, payload: _response_with_finish("max_tokens"),
        )


def test_finish_reason_stop_returns_content(tmp_path):
    result = _generate(
        tmp_path,
        post=lambda url, headers, payload: _response_with_finish("stop", "# Итоги"),
    )
    assert result.markdown == "# Итоги"


def test_absent_finish_reason_returns_content(tmp_path):
    result = _generate(
        tmp_path,
        post=lambda url, headers, payload: _ok_response("# Итоги"),
    )
    assert result.markdown == "# Итоги"


def _mock_transport(monkeypatch, handler):
    """Make _http_post's httpx.Client answer via `handler` instead of the network."""
    real_client = httpx.Client  # bind before patching, or the stand-in recurses
    monkeypatch.setattr(
        httpx, "Client", lambda *a, **kw: real_client(transport=httpx.MockTransport(handler))
    )


def _post(monkeypatch, handler):
    _mock_transport(monkeypatch, handler)
    return _http_post("https://x/v1/chat/completions", {}, {"model": "m"})


def test_http_post_returns_parsed_json(monkeypatch):
    data = _post(monkeypatch, lambda req: httpx.Response(200, json=_ok_response()))

    assert data == _ok_response()


def test_http_post_error_message_carries_provider_reason(monkeypatch):
    """A bare '403 Forbidden' in the logs hides the reason — the body has it."""
    body = {"error": {"message": "Key limit exceeded (total limit).", "code": 403}}

    with pytest.raises(LlmError) as excinfo:
        _post(monkeypatch, lambda req: httpx.Response(403, json=body))

    message = str(excinfo.value)
    assert "403" in message
    assert "Key limit exceeded (total limit)." in message


def test_http_post_truncates_a_long_error_body(monkeypatch):
    html = "<html>" + "x" * 5000 + "</html>"

    with pytest.raises(LlmError) as excinfo:
        _post(monkeypatch, lambda req: httpx.Response(502, text=html))

    message = str(excinfo.value)
    assert len(message) < 1000
    assert "…" in message


def test_http_post_empty_error_body_still_raises(monkeypatch):
    with pytest.raises(LlmError, match="401"):
        _post(monkeypatch, lambda req: httpx.Response(401, text="   "))


def test_http_post_connection_failure_raises_without_a_response(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(LlmError, match="connection refused"):
        _post(monkeypatch, refuse)
