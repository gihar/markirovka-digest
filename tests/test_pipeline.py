"""Behavior: the pipeline publishes only when there are messages to digest."""

from datetime import date
from pathlib import Path

import pytest

from main import run_and_alert_on_failure, run_pipeline
from models import DigestResult, TelegramMessage
from datetime import UTC, datetime

DAY = date(2026, 7, 4)


def _msg():
    return TelegramMessage(
        chat_id=-1001,
        chat_title="Маркировка",
        sender_name="ivan",
        text="про маркировку",
        date=datetime(2026, 7, 4, 10, 0, tzinfo=UTC),
    )


def _digest():
    return DigestResult(
        date="2026-07-04",
        markdown="итоги",
        message_count=1,
        chat_count=1,
        token_count=1,
        model="anthropic/claude-sonnet-4.6",
    )


def test_empty_day_does_not_publish():
    calls = {"generated": 0, "published": 0}

    def generate(messages, prompt_path, date_str):
        calls["generated"] += 1
        return _digest()

    def publish_digest(digest):
        calls["published"] += 1
        return 1

    result = run_pipeline(
        day=DAY,
        prompt_path=Path("prompts/digest.md"),
        fetch_messages=lambda: [],
        generate=generate,
        publish_digest=publish_digest,
    )

    assert result is None
    assert calls == {"generated": 0, "published": 0}


def test_non_empty_day_generates_and_publishes():
    seen = {}

    def generate(messages, prompt_path, date_str):
        seen["messages"] = messages
        seen["date_str"] = date_str
        return _digest()

    def publish_digest(digest):
        seen["digest"] = digest
        return 2  # two Telegram parts

    result = run_pipeline(
        day=DAY,
        prompt_path=Path("prompts/digest.md"),
        fetch_messages=lambda: [_msg()],
        generate=generate,
        publish_digest=publish_digest,
    )

    assert result == 2
    assert seen["date_str"] == "2026-07-04"  # the covered MSK day
    assert seen["digest"].markdown == "итоги"


def test_a_failed_run_alerts_once_and_still_fails():
    # The alert is a side note, never a substitute: the exception has to keep
    # propagating or the cron run exits 0 and the platform reports success.
    alerted = []
    boom = RuntimeError("Every LLM provider failed")

    def work():
        raise boom

    with pytest.raises(RuntimeError) as caught:
        run_and_alert_on_failure(work, alerted.append)

    assert caught.value is boom  # the original error, not the alert's
    assert alerted == [boom]  # exactly one alert


def test_a_successful_run_alerts_nobody():
    alerted = []

    result = run_and_alert_on_failure(lambda: 2, alerted.append)

    assert result == 2
    assert alerted == []
