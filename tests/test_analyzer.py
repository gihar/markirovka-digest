"""Behavior: messages are grouped by chat and timestamped in Moscow time."""

from datetime import UTC, datetime

from analyzer import prepare_messages_markdown
from models import TelegramMessage


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
