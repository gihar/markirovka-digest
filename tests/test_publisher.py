"""Behavior: send a rendered Digest to Telegram, and alert on failure."""

import re
from datetime import date
from pathlib import Path

import pytest

from config import Config
from models import DigestResult, LlmProvider
from publisher import (
    _TELEGRAM_SEND_INTERVAL,
    alert_degraded,
    alert_failure,
    TelegramDeliveryError,
    TelegramFloodError,
    render_alert,
    render_degraded_alert,
    send_parts,
)
from render import TELEGRAM_LIMIT

_NO_SLEEP = lambda *_: None  # noqa: E731 — instant tests, no real waiting


def _digest(markdown: str) -> DigestResult:
    return DigestResult(
        date="2026-07-04",
        markdown=markdown,
        message_count=1,
        chat_count=1,
        token_count=1,
        model="anthropic/claude-sonnet-4.6",
        provider_failures=(),
    )


def test_send_parts_posts_each_part_with_html_payload():
    calls = []
    send_parts(
        ["часть1", "часть2"],
        token="123:abc",
        chat_id="-1009999",
        post=lambda url, payload: calls.append((url, payload)),
        sleep=_NO_SLEEP,
    )
    assert [c[0] for c in calls] == [
        "https://api.telegram.org/bot123:abc/sendMessage",
        "https://api.telegram.org/bot123:abc/sendMessage",
    ]
    assert [c[1] for c in calls] == [
        {"chat_id": "-1009999", "text": "часть1", "parse_mode": "HTML"},
        {"chat_id": "-1009999", "text": "часть2", "parse_mode": "HTML"},
    ]


def test_send_parts_attempts_every_part_then_raises_on_failure():
    sent = []

    def flaky(url, payload):
        if payload["text"] == "boom":
            raise RuntimeError("network")
        sent.append(payload["text"])

    with pytest.raises(TelegramDeliveryError):
        send_parts(
            ["ok1", "boom", "ok2"], token="t", chat_id="-1",
            post=flaky, sleep=_NO_SLEEP,
        )

    # Remaining parts are still attempted before the failure is surfaced.
    assert sent == ["ok1", "ok2"]


def test_send_parts_returns_count_when_all_succeed():
    count = send_parts(
        ["a", "b"], token="t", chat_id="-1",
        post=lambda url, payload: None, sleep=_NO_SLEEP,
    )
    assert count == 2


def test_send_parts_paces_between_parts():
    slept = []
    send_parts(
        ["a", "b", "c"], token="t", chat_id="-1",
        post=lambda url, payload: None, sleep=slept.append,
    )
    # One pacing pause per gap between the 3 parts (none before the first).
    assert slept == [_TELEGRAM_SEND_INTERVAL, _TELEGRAM_SEND_INTERVAL]


def test_send_parts_honors_retry_after_then_succeeds():
    slept = []
    attempts = []

    def flooded_once(url, payload):
        attempts.append(payload["text"])
        # "b" floods on its first attempt, then goes through.
        if payload["text"] == "b" and attempts.count("b") == 1:
            raise TelegramFloodError(retry_after=5.0)

    count = send_parts(
        ["a", "b"], token="t", chat_id="-1",
        post=flooded_once, sleep=slept.append, max_flood_retries=2,
    )

    assert count == 2  # 'b' recovered after honoring retry_after
    assert 5.0 in slept  # waited the retry_after before retrying
    assert attempts == ["a", "b", "b"]


def test_send_parts_gives_up_after_persistent_flood():
    def always_flood(url, payload):
        raise TelegramFloodError(retry_after=1.0)

    with pytest.raises(TelegramDeliveryError):
        send_parts(
            ["a"], token="t", chat_id="-1",
            post=always_flood, sleep=_NO_SLEEP, max_flood_retries=2,
        )


def _config(alert_chat_id: str | None, primary_model: str = "m") -> Config:
    """A Config that differs from the real one only in the alert chat."""
    return Config(
        database_url="postgresql://u:p@host:5432/db",
        llm_providers=(
            LlmProvider(base_url="u", api_key="k", model=primary_model),
        ),
        telegram_bot_token="123:abc",
        telegram_digest_chat_id="-1001383199989",
        telegram_alert_chat_id=alert_chat_id,
        channels=(),
        prompt_path=Path("prompts/digest.md"),
        min_message_length=30,
        digest_channel_id=-1001383199989,
    )


_PRIMARY_MODEL = "anthropic/claude-sonnet-4.6"


_PRIMARY_FAILED = (f"[{_PRIMARY_MODEL}] LLM request failed: 403 Key limit exceeded",)


def _digest_served_by(
    model: str | None, failures: tuple[str, ...] = ()
) -> DigestResult:
    """A digest produced by ``model``, having skipped the providers in ``failures``."""
    return DigestResult(
        date="2026-08-09",
        markdown="итоги",
        message_count=1,
        chat_count=1,
        token_count=1,
        model=model,
        provider_failures=failures,
    )


def test_a_fallback_served_digest_alerts_with_the_model_and_the_reason():
    calls = []

    alert_degraded(
        date(2026, 8, 9),
        _digest_served_by("qwen3.6-unlim-noreason", _PRIMARY_FAILED),
        _config("-1005555", primary_model=_PRIMARY_MODEL),
        post=lambda url, payload: calls.append(payload),
        sleep=_NO_SLEEP,
    )

    assert len(calls) == 1  # exactly one message
    assert calls[0]["chat_id"] == "-1005555"
    text = calls[0]["text"]
    assert "09.08.2026" in text
    assert "qwen3.6-unlim-noreason" in text  # what produced the digest
    assert "Key limit exceeded" in text  # why the primary was skipped


def test_a_primary_served_digest_alerts_nobody():
    """A normal day must stay silent, or the alert becomes noise to be ignored."""
    calls = []

    alert_degraded(
        date(2026, 8, 9),
        _digest_served_by(_PRIMARY_MODEL),
        _config("-1005555", primary_model=_PRIMARY_MODEL),
        post=lambda url, payload: calls.append(payload),
        sleep=_NO_SLEEP,
    )

    assert calls == []


def test_a_day_with_nothing_to_digest_is_not_a_degraded_run():
    """model is None means no LLM ran at all — that is quiet, not degraded."""
    calls = []

    alert_degraded(
        date(2026, 8, 9),
        _digest_served_by(None),
        _config("-1005555", primary_model=_PRIMARY_MODEL),
        post=lambda url, payload: calls.append(payload),
        sleep=_NO_SLEEP,
    )

    assert calls == []


def test_no_degraded_alert_is_attempted_when_the_alert_chat_is_unset(caplog):
    """The fallback must keep working unalerted — the digest is already out."""
    calls = []

    alert_degraded(
        date(2026, 8, 9),
        _digest_served_by("qwen3.6-unlim-noreason", _PRIMARY_FAILED),
        _config(None, primary_model=_PRIMARY_MODEL),
        post=lambda url, payload: calls.append(payload),
        sleep=_NO_SLEEP,
    )

    assert calls == []  # nowhere to send
    assert "TELEGRAM_ALERT_CHAT_ID" in caplog.text
    assert "qwen3.6-unlim-noreason" in caplog.text  # degradation still recorded


def test_the_degraded_alert_reads_unlike_the_failure_alert():
    """"No digest" and "digest on the backup" call for different reactions."""
    day = date(2026, 8, 9)

    degraded = render_degraded_alert(
        day, _digest_served_by("qwen3.6-unlim-noreason", _PRIMARY_FAILED)
    )
    failed = render_alert(day, RuntimeError("Every LLM provider failed"))

    assert "опубликован на запасном" in degraded
    assert "не опубликован" not in degraded
    assert "не опубликован" in failed
    assert degraded.splitlines()[0] != failed.splitlines()[0]


def test_a_failed_degraded_alert_send_never_escapes(caplog):
    """The digest is already published — a sulking Telegram must not fail the run."""

    def broken(url, payload):
        raise RuntimeError("Telegram недоступен")

    alert_degraded(
        date(2026, 8, 9),
        _digest_served_by("qwen3.6-unlim-noreason", _PRIMARY_FAILED),
        _config("-1005555", primary_model=_PRIMARY_MODEL),
        post=broken,
        sleep=_NO_SLEEP,
    )

    assert "Telegram недоступен" in caplog.text


def test_alert_names_the_covered_day_and_the_error():
    calls = []

    alert_failure(
        date(2026, 8, 9),
        RuntimeError("Every LLM provider failed: [gpt] 403 Key limit exceeded"),
        _config("-1005555"),
        post=lambda url, payload: calls.append(payload),
        sleep=_NO_SLEEP,
    )

    assert len(calls) == 1  # exactly one message, not a split digest
    assert calls[0]["chat_id"] == "-1005555"
    text = calls[0]["text"]
    assert "09.08.2026" in text
    assert "Every LLM provider failed: [gpt] 403 Key limit exceeded" in text


def test_no_alert_is_attempted_when_the_alert_chat_is_unset(caplog):
    calls = []

    alert_failure(
        date(2026, 8, 9),
        RuntimeError("боль"),
        _config(None),
        post=lambda url, payload: calls.append(payload),
        sleep=_NO_SLEEP,
    )

    assert calls == []  # not even an attempt — there is nowhere to send
    assert "TELEGRAM_ALERT_CHAT_ID" in caplog.text


def test_a_failed_alert_send_never_escapes(caplog):
    # The caller re-raises the original failure right after alerting. If the
    # alert raised, it would replace the reason the digest died with the reason
    # Telegram was unhappy — the log would name the wrong culprit.
    def broken(url, payload):
        raise RuntimeError("Telegram недоступен")

    alert_failure(
        date(2026, 8, 9),
        RuntimeError("исходная причина провала"),
        _config("-1005555"),
        post=broken,
        sleep=_NO_SLEEP,
    )

    assert "Telegram недоступен" in caplog.text


def test_alert_escapes_html_in_the_error_message():
    # The alert goes out with parse_mode=HTML, and a provider error body can
    # itself be an HTML page from a proxy. Unescaped, Telegram rejects the whole
    # message with a 400 — the alert about a failure would fail silently.
    calls = []

    alert_failure(
        date(2026, 8, 9),
        RuntimeError("<html>502 Bad Gateway</html> & retry"),
        _config("-1005555"),
        post=lambda url, payload: calls.append(payload),
        sleep=_NO_SLEEP,
    )

    text = calls[0]["text"]
    assert "<html>" not in text
    assert "&lt;html&gt;502 Bad Gateway&lt;/html&gt; &amp; retry" in text
    assert "<b>" in text  # our own markup still renders


def test_a_huge_error_message_still_fits_one_telegram_message():
    # Worst case for escaping: every '&' becomes '&amp;', five chars for one.
    # An over-limit message is rejected outright, so the alert must clip itself.
    calls = []

    alert_failure(
        date(2026, 8, 9),
        RuntimeError("&" * 20_000),
        _config("-1005555"),
        post=lambda url, payload: calls.append(payload),
        sleep=_NO_SLEEP,
    )

    assert len(calls) == 1  # still one message, never split into many
    text = calls[0]["text"]
    assert len(text) <= TELEGRAM_LIMIT
    # A clip landing inside "&amp;" would leave a bare '&' and break the parse.
    assert not re.search(r"&[#0-9a-zA-Z]*$", text)


def test_alert_does_not_escape_quotes():
    # Telegram only requires <, > and & escaped in text; quotes need nothing.
    # Provider error bodies are JSON and full of them, so escaping quotes would
    # spend the message budget on '&quot;' without changing what a reader sees.
    calls = []

    alert_failure(
        date(2026, 8, 9),
        RuntimeError('{"error":{"message":"Key limit exceeded"}}'),
        _config("-1005555"),
        post=lambda url, payload: calls.append(payload),
        sleep=_NO_SLEEP,
    )

    text = calls[0]["text"]
    assert '{"error":{"message":"Key limit exceeded"}}' in text
    assert "&quot;" not in text


def test_an_alert_can_name_what_was_not_published():
    text = render_alert(
        date(2026, 10, 1), RuntimeError("boom"),
        subject="Обзор недели 25.09–01.10.2026",
    )
    assert text.startswith("⚠️ <b>Обзор недели 25.09–01.10.2026 не опубликован</b>")


def test_a_degraded_alert_can_name_what_was_published():
    digest = DigestResult(**{**_digest("x").__dict__, "model": "qwen"})
    text = render_degraded_alert(
        date(2026, 10, 1), digest, subject="Обзор недели 25.09–01.10.2026"
    )
    assert text.startswith(
        "🟡 <b>Обзор недели 25.09–01.10.2026 опубликован на запасном провайдере</b>"
    )
