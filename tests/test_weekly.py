"""Behavior: the weekly review is built from the week's published Digests."""

from datetime import date
from pathlib import Path

from digest_archive import ChannelPost
from models import DigestResult, LlmProvider
from weekly import (
    generate_weekly_review,
    run_weekly_pipeline,
    week_digests,
    weekly_input,
    weekly_title,
)

START, END = date(2026, 9, 25), date(2026, 10, 1)
MAIN = -1001205001393


def _digest_post(day: date, body: str) -> ChannelPost:
    title = f"🗓 Дайджест чатов по маркировке за {day.strftime('%d.%m.%Y')}"
    return ChannelPost(chat_id=MAIN, text=f"{title}\n\n{body}")


def _provider(model="anthropic/claude-sonnet-4.6"):
    return LlmProvider(base_url="https://x/v1", api_key="k", model=model)


def _prompt(tmp_path) -> Path:
    p = tmp_path / "weekly.md"
    p.write_text("Ты аналитик.", encoding="utf-8")
    return p


def _review() -> DigestResult:
    return DigestResult(
        date=END.isoformat(), markdown="итоги недели", message_count=5,
        chat_count=0, token_count=1, model="m", provider_failures=(),
    )


def test_every_published_day_of_the_week_is_collected_in_order():
    posts = [
        _digest_post(date(2026, 9, 25), "пятница"),
        ChannelPost(chat_id=MAIN, text="Мониторинг маркировки: 26.09.2026"),
        _digest_post(date(2026, 9, 27), "воскресенье"),
        _digest_post(date(2026, 10, 1), "четверг"),
        _digest_post(date(2026, 10, 2), "уже следующая неделя"),
    ]

    digests = week_digests(posts, START, END)

    assert [day for day, _ in digests] == [
        date(2026, 9, 25), date(2026, 9, 27), date(2026, 10, 1),
    ]
    assert "воскресенье" in digests[1][1]


def test_the_model_input_is_one_section_per_day():
    md = weekly_input([(date(2026, 9, 25), "текст пятницы"), (date(2026, 9, 27), "текст")])

    assert md.startswith("## Дайджест за 25.09.2026\n\nтекст пятницы")
    assert "## Дайджест за 27.09.2026\n\nтекст" in md


def test_the_title_names_the_week():
    assert weekly_title(START, END) == "📊 **Обзор недели по маркировке: 25.09–01.10.2026**"


def test_the_review_comes_from_the_provider_chain(tmp_path):
    captured = {}

    def fake_post(url, headers, payload):
        captured.update(payload=payload)
        return {"choices": [{"message": {"content": "🔁 **Тема** #молоко #сыр"}}]}

    review = generate_weekly_review(
        [(date(2026, 9, 25), "текст пятницы")], _prompt(tmp_path), END,
        providers=(_provider(),), chat_hashtags={1: "#молоко"}, post=fake_post,
    )

    assert review.markdown == "🔁 **Тема** #молоко"
    assert review.message_count == 1  # digests read
    assert review.model == "anthropic/claude-sonnet-4.6"
    assert "текст пятницы" in captured["payload"]["messages"][1]["content"]


def test_a_week_without_digests_publishes_nothing():
    calls = []

    result = run_weekly_pipeline(
        fetch_digests=lambda: [],
        generate=lambda digests: calls.append("generate"),
        publish_review=lambda review: calls.append("publish"),
        report_review=lambda review: calls.append("report"),
    )

    assert result is None
    assert calls == []


def test_a_week_with_digests_is_generated_published_and_reported():
    calls = []
    digests = [(START, "текст")]

    def generate(got):
        assert got == digests
        calls.append("generate")
        return _review()

    result = run_weekly_pipeline(
        fetch_digests=lambda: digests,
        generate=generate,
        publish_review=lambda review: calls.append("publish") or 2,
        report_review=lambda review: calls.append("report"),
    )

    assert result == 2
    assert calls == ["generate", "publish", "report"]
