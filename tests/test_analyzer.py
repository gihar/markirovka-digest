"""Behavior: format messages, then generate the digest via an OpenAI-compatible LLM."""

import logging
from datetime import UTC, datetime

import httpx
import pytest

from analyzer import LlmError, _http_post, generate_digest, prepare_messages_markdown
from models import LlmProvider, TelegramMessage


def _msg(chat="Маркировка. Молоко", text="привет"):
    return TelegramMessage(-1, chat, "ivan", text, datetime(2026, 7, 4, 10, 0, tzinfo=UTC))


def _provider(
    base_url="https://openrouter.ai/api/v1",
    api_key="sk-or-xxx",
    model="anthropic/claude-sonnet-4.6",
):
    return LlmProvider(base_url=base_url, api_key=api_key, model=model)


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
    assert "13:00] **ivan**: привет" in md
    assert "10:30] **petr**: вопрос" in md


def _addressed(message_id, chat="Маркировка. Молоко", text="привет",
               hour=10, username="markirovka_moloko"):
    return TelegramMessage(
        -1001359438834, chat, "ivan", text,
        datetime(2026, 7, 4, hour, 0, tzinfo=UTC),
        message_id=message_id, chat_username=username,
    )


def test_every_message_gets_a_reference_the_model_can_cite():
    md = prepare_messages_markdown([_addressed(1, hour=10), _addressed(2, hour=11)])

    assert "[m1 · 13:00] **ivan**: привет" in md
    assert "[m2 · 14:00] **ivan**: привет" in md


def test_a_chat_header_names_its_industry_hashtag():
    md = prepare_messages_markdown(
        [_addressed(1)], chat_hashtags={-1001359438834: "#молоко"}
    )

    assert "## Маркировка. Молоко · #молоко" in md


def test_a_chat_without_a_hashtag_keeps_a_bare_header():
    md = prepare_messages_markdown([_addressed(1)], chat_hashtags={})

    assert "## Маркировка. Молоко\n" in md


def test_hashtags_outside_the_fixed_set_are_removed_from_the_digest(tmp_path):
    result = generate_digest(
        [_addressed(1)], _prompt(tmp_path), "2026-07-04",
        providers=(_provider(),),
        chat_hashtags={-1001359438834: "#молоко"},
        post=lambda *_: _ok_response(
            "✅ **Тема** (Молоко) #молоко #сыр [[m1]]\nпункт #1 и ## не тег"
        ),
    )

    assert result.markdown == "✅ **Тема** (Молоко) #молоко [[m1]]\nпункт #1 и ## не тег"


def _reply(message_id, reply_to, hour, sender="petr"):
    return TelegramMessage(
        -1001359438834, "Маркировка. Молоко", sender, "ответ",
        datetime(2026, 7, 4, hour, 0, tzinfo=UTC),
        message_id=message_id, reply_to_message_id=reply_to,
    )


def test_a_reply_names_the_message_it_answers():
    md = prepare_messages_markdown([_addressed(1, hour=10), _reply(2, 1, hour=11)])

    assert "[m2 · 14:00] **petr** ↳ m1 (ivan, 13:00): ответ" in md


def test_a_reply_to_a_message_outside_the_input_is_still_marked():
    # The parent was sent yesterday, filtered as too short, or spam.
    md = prepare_messages_markdown([_reply(2, 999, hour=11)])

    assert "[m1 · 14:00] **petr** ↳ ответ на сообщение вне выборки: ответ" in md


def test_a_reply_never_matches_a_message_of_another_chat():
    other_chat = TelegramMessage(
        -1001205001393, "Маркировка. Главный чат", "ivan", "вопрос",
        datetime(2026, 7, 4, 9, 0, tzinfo=UTC), message_id=1,
    )
    md = prepare_messages_markdown([other_chat, _reply(2, 1, hour=11)])

    assert "↳ ответ на сообщение вне выборки" in md


def test_an_ordinary_message_has_no_reply_mark():
    md = prepare_messages_markdown([_addressed(1)])

    assert "↳" not in md


def test_digest_carries_a_link_for_every_referenced_message(tmp_path):
    result = generate_digest(
        [_addressed(185041)], _prompt(tmp_path), "2026-07-04",
        providers=(_provider(),),
        post=lambda *_: _ok_response("✅ **Тема** (Молоко) [[m1]]"),
    )

    assert result.links == {"m1": "https://t.me/markirovka_moloko/185041"}


def test_a_message_without_an_address_gets_no_link(tmp_path):
    result = generate_digest(
        [_msg()], _prompt(tmp_path), "2026-07-04",
        providers=(_provider(),),
        post=lambda *_: _ok_response(),
    )

    assert result.links == {}


def test_generate_digest_posts_openai_payload_and_returns_content(tmp_path):
    captured = {}

    def fake_post(url, headers, payload):
        captured.update(url=url, headers=headers, payload=payload)
        return _ok_response("# Дайджест\nитоги дня")

    result = generate_digest(
        [_msg()], _prompt(tmp_path), "2026-07-04",
        providers=(_provider(),),
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


def test_first_provider_produces_the_digest_and_the_next_is_untouched(tmp_path):
    tried = []

    def fake_post(url, headers, payload):
        tried.append(payload["model"])
        return _ok_response("# Дайджест")

    result = generate_digest(
        [_msg()], _prompt(tmp_path), "2026-07-04",
        providers=(_provider(model="primary"), _provider(model="fallback")),
        post=fake_post,
    )

    assert result.markdown == "# Дайджест"
    assert result.model == "primary"
    assert result.provider_failures == ()
    assert tried == ["primary"]


def test_a_failed_provider_is_logged_and_the_next_one_is_tried(tmp_path, caplog):
    """A provider's billing state must not take the whole run down (ADR-0002)."""
    tried = []

    def fake_post(url, headers, payload):
        tried.append(payload["model"])
        if payload["model"] == "primary":
            raise LlmError("LLM request failed: 403 Key limit exceeded")
        return _ok_response("# Запасной дайджест")

    with caplog.at_level(logging.WARNING):
        result = generate_digest(
            [_msg()], _prompt(tmp_path), "2026-07-04",
            providers=(_provider(model="primary"), _provider(model="fallback")),
            post=fake_post,
        )

    assert result.markdown == "# Запасной дайджест"
    assert result.model == "fallback"
    assert tried == ["primary", "fallback"]
    assert "primary" in caplog.text
    assert "Key limit exceeded" in caplog.text


def test_the_fallback_digest_records_why_the_primary_was_skipped(tmp_path):
    """Only this loop sees the reason, and the degraded-run alert has to name it."""

    def fake_post(url, headers, payload):
        if payload["model"] == "primary":
            raise LlmError("LLM request failed: 403 Key limit exceeded")
        return _ok_response("# Запасной дайджест")

    result = generate_digest(
        [_msg()], _prompt(tmp_path), "2026-07-04",
        providers=(_provider(model="primary"), _provider(model="fallback")),
        post=fake_post,
    )

    assert result.model == "fallback"
    assert result.provider_failures == (
        "[primary] LLM request failed: 403 Key limit exceeded",
    )


def test_when_every_provider_fails_the_error_names_them_all(tmp_path):
    """The run must still exit non-zero, and say which provider failed how."""

    def fake_post(url, headers, payload):
        raise LlmError(f"LLM request failed: 403 for {payload['model']}")

    with pytest.raises(LlmError) as excinfo:
        generate_digest(
            [_msg()], _prompt(tmp_path), "2026-07-04",
            providers=(_provider(model="primary"), _provider(model="fallback")),
            post=fake_post,
        )

    message = str(excinfo.value)
    assert "primary" in message
    assert "fallback" in message
    assert "403" in message


def test_generate_digest_without_providers_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="at least one"):
        generate_digest(
            [_msg()], _prompt(tmp_path), "2026-07-04",
            providers=(),
            post=lambda url, headers, payload: _ok_response(),
        )


def _generate(tmp_path, post):
    return generate_digest(
        [_msg()], _prompt(tmp_path), "2026-07-04",
        providers=(_provider("https://x/v1", "k", "m"),), post=post,
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
