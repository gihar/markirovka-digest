"""Behavior: publish the Digest to Telegram, splitting long text losslessly."""

import re
from datetime import date
from pathlib import Path

import pytest

from config import Config
from models import DigestResult, LlmProvider
from publisher import (
    _TELEGRAM_LIMIT,
    _TELEGRAM_SEND_INTERVAL,
    alert_degraded,
    alert_failure,
    TelegramDeliveryError,
    TelegramFloodError,
    markdown_to_telegram_html,
    render_alert,
    render_degraded_alert,
    render_parts,
    send_parts,
    split_message,
)

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


def test_short_text_is_a_single_part():
    assert split_message("привет", limit=4096) == ["привет"]


def test_text_exactly_at_limit_is_a_single_part():
    text = "x" * 10
    assert split_message(text, limit=10) == [text]


def test_splits_on_line_boundaries_without_breaking_lines():
    lines = [f"line-{i}" for i in range(6)]  # each 6 chars
    text = "\n".join(lines)
    parts = split_message(text, limit=15)  # ~2 lines per part

    assert all(len(p) <= 15 for p in parts)
    assert len(parts) > 1
    # No line is broken and order/content is preserved.
    reassembled = [ln for p in parts for ln in p.split("\n")]
    assert reassembled == lines


def test_hard_splits_a_single_oversized_line():
    text = "y" * 25
    parts = split_message(text, limit=10)

    assert [len(p) for p in parts] == [10, 10, 5]
    assert "".join(parts) == text


def test_no_content_is_lost_when_splitting():
    text = "\n".join(["абвгде" * 3] * 20)  # forces many splits
    parts = split_message(text, limit=40)

    assert all(len(p) <= 40 for p in parts)
    # Every non-newline character is preserved, in order.
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")


def test_markdown_to_html_escapes_then_formats():
    out = markdown_to_telegram_html("## Заголовок & <b>\n**жирный** и *курсив*")
    assert "<b>Заголовок &amp; &lt;b&gt;</b>" in out
    assert "<b>жирный</b>" in out
    assert "<i>курсив</i>" in out


def test_markdown_escape_backslashes_are_stripped():
    # LLMs habitually escape markdown punctuation (tasy\_emdina); Telegram HTML
    # needs the plain character, not a literal backslash.
    out = markdown_to_telegram_html("**tasy\\_emdina** и KALITIN\\_VLADIMIR")
    assert "<b>tasy_emdina</b>" in out
    assert "KALITIN_VLADIMIR" in out
    assert "\\" not in out


def test_escaped_punctuation_in_prose_renders_plain():
    out = markdown_to_telegram_html("до 01\\.08\\.2026 \\(ЕАЭС\\) — срок")
    assert "до 01.08.2026 (ЕАЭС) — срок" in out


def test_escaped_asterisk_is_left_alone():
    # '*' is real markup for this converter; its escape is not stripped.
    out = markdown_to_telegram_html("знак \\* не курсив")
    assert "\\*" in out


def test_a_known_reference_renders_as_a_discussion_link():
    out = markdown_to_telegram_html(
        "✅ **Тема** (Молоко) [[m3]]",
        links={"m3": "https://t.me/markirovka_moloko/42"},
    )
    assert out == (
        '✅ <b>Тема</b> (Молоко) '
        '<a href="https://t.me/markirovka_moloko/42">→ обсуждение</a>'
    )


def test_an_unknown_reference_is_dropped_not_left_broken():
    out = markdown_to_telegram_html("✅ **Тема** (Молоко) [[m99]]", links={})
    assert out == "✅ <b>Тема</b> (Молоко)"


def test_a_url_written_by_the_model_is_never_made_a_link():
    out = markdown_to_telegram_html("см. [тут](https://evil.example/x)", links={})
    assert "<a" not in out


def test_render_parts_links_references_from_the_digest():
    digest = _digest("✅ **Тема** [[m1]]")
    digest = DigestResult(**{**digest.__dict__, "links": {"m1": "https://t.me/c/1/2"}})
    [part] = render_parts(digest)
    assert '<a href="https://t.me/c/1/2">→ обсуждение</a>' in part


def test_render_parts_prepends_dated_header():
    parts = render_parts(_digest("**Итоги** дня"))
    assert parts[0].startswith("🗓 <b>Дайджест чатов по маркировке за 04.07.2026</b>")
    assert "<b>Итоги</b> дня" in parts[0]


def test_render_parts_short_digest_is_one_html_part():
    parts = render_parts(_digest("**Итоги** дня"), limit=4096)
    assert parts == ["🗓 <b>Дайджест чатов по маркировке за 04.07.2026</b>\n\n<b>Итоги</b> дня"]


def test_render_parts_strips_horizontal_rules():
    parts = render_parts(_digest("Раздел 1\n\n---\n\nРаздел 2\n\n***\n\nРаздел 3"))
    body = parts[0]
    assert "---" not in body
    assert "***" not in body
    assert "Раздел 1" in body and "Раздел 2" in body and "Раздел 3" in body
    assert "\n\n\n" not in body  # blank runs collapsed, no gaping gaps


def test_dated_header_only_on_first_part_when_split():
    long_md = "\n".join(f"строка {i}" for i in range(300))
    parts = render_parts(_digest(long_md), limit=200)
    assert len(parts) > 1
    assert parts[0].startswith("🗓 <b>Дайджест чатов по маркировке за 04.07.2026</b>")
    assert all("Дайджест чатов по маркировке за" not in p for p in parts[1:])


def test_render_parts_long_digest_is_split_under_limit():
    long_md = "\n".join([f"Пункт {i}: обсуждение маркировки" for i in range(200)])
    parts = render_parts(_digest(long_md), limit=500)
    assert len(parts) > 1
    assert all(len(p) <= 500 for p in parts)


def test_continuation_marker_on_part_after_first_when_split():
    long_md = "\n".join(f"строка {i}" for i in range(300))
    parts = render_parts(_digest(long_md), limit=200)
    assert len(parts) > 1
    assert not parts[0].startswith("<i>(продолжение)</i>")
    assert parts[1].startswith("<i>(продолжение)</i>\n\n")


def test_continuation_marker_is_reserved_so_parts_stay_under_limit():
    # Uniform 2-char lines pack to exactly the limit under a naive full-limit
    # split (3k-1 == 200 at k=67), so blindly prepending the 22-char marker
    # afterwards would push a part to 222 > 200. The marker must be reserved.
    limit = 200
    parts = render_parts(_digest("\n".join(["ab"] * 400)), limit=limit)
    assert len(parts) > 1
    assert parts[1].startswith("<i>(продолжение)</i>\n\n")
    assert all(len(p) <= limit for p in parts)


def test_continuation_marker_on_every_part_after_the_first():
    long_md = "\n".join(f"строка номер {i}" for i in range(300))
    parts = render_parts(_digest(long_md), limit=200)
    assert len(parts) >= 3  # at least three messages, to exercise parts[2]+
    assert not parts[0].startswith("<i>(продолжение)</i>")
    assert all(p.startswith("<i>(продолжение)</i>\n\n") for p in parts[1:])


def test_single_part_digest_has_no_continuation_marker():
    parts = render_parts(_digest("**Итоги** дня"), limit=4096)
    assert len(parts) == 1
    assert "(продолжение)" not in parts[0]


def test_split_digest_content_is_lossless_after_removing_markers():
    # Removing the injected header and every continuation marker must leave the
    # original digest content intact (compared on non-newline chars, since a
    # split drops the boundary newline just as it did before this change).
    lines = [f"строка {i}" for i in range(300)]
    body_md = "\n".join(lines)
    parts = render_parts(_digest(body_md), limit=200)

    header = "🗓 <b>Дайджест чатов по маркировке за 04.07.2026</b>"
    marker = "<i>(продолжение)</i>"
    joined = "".join(parts).replace(header, "", 1).replace(marker, "")
    assert joined.replace("\n", "") == body_md.replace("\n", "")


def test_oversized_line_never_cuts_an_html_entity():
    # One line (no newlines) far over the limit, full of '&' — each escapes to
    # '&amp;'. A naive hard-split of the HTML would sever an entity, which
    # Telegram rejects under parse_mode=HTML.
    parts = render_parts(_digest("&" * 300), limit=100)

    assert len(parts) > 1
    assert all(len(p) <= 100 for p in parts)
    # Every '&' is part of a complete '&amp;' — none was cut.
    assert all(p.replace("&amp;", "").count("&") == 0 for p in parts)
    assert "".join(parts).count("&amp;") == 300  # nothing lost


def test_oversized_bold_line_splits_without_malformed_tags():
    md = "**" + ("слово " * 200).strip() + "**"
    parts = render_parts(_digest(md), limit=120)

    assert len(parts) > 1
    for p in parts:
        assert len(p) <= 120
        assert p.count("<") == p.count(">")  # no half-cut tag


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
    assert len(text) <= _TELEGRAM_LIMIT
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
