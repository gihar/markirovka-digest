"""Behavior: the pipeline publishes only when there are messages to digest."""

from datetime import date
from pathlib import Path

from main import run_pipeline
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
        date="2026-07-04", markdown="итоги", message_count=1, chat_count=1, token_count=1
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
