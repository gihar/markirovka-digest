"""Content contract for the digest system prompt.

Locks the redesign where a THEME is the only structural unit of the digest and a
theme's status is an emoji attribute of its heading — not a separate section.
The prompt is loaded exactly as production does (``config.PROMPT_PATH`` through
``analyzer._load_prompt``) so these tests exercise the file that actually ships.
"""

import re

from analyzer import _load_prompt
from config import PROMPT_PATH, WEEKLY_PROMPT_PATH


def _prompt() -> str:
    return _load_prompt(PROMPT_PATH)


def test_removed_sections_are_absent():
    """The redesign drops the sections that re-told the same theme pool."""
    prompt = _prompt()

    assert "Статистика активности" not in prompt
    assert "Тренды и наблюдения" not in prompt
    assert "Проблемы и вопросы без ответа" not in prompt
    assert "Вывод/решение" not in prompt


def test_status_emoji_vocabulary_is_defined():
    """A theme's status lives in its heading as one of three emoji."""
    prompt = _prompt()

    assert "✅" in prompt  # решение найдено / консенсус достигнут
    assert "❓" in prompt  # вопрос остался без ответа
    assert "🔁" in prompt  # тема продолжается / тянется не первый день


def test_kratko_footer_convention_is_present():
    """Minor topics fold into the optional «Кратко» footer line."""
    prompt = _prompt()

    assert "Кратко" in prompt


def test_no_numbered_section_scaffolding():
    """The numbered «1.», «2.» headings leaked into output, so they are gone."""
    prompt = _prompt()

    assert not re.search(r"^#+\s*\d+\.", prompt, re.MULTILINE)


def test_themes_are_ordered_by_business_impact():
    """A reader who stops after three themes has still seen what matters most."""
    prompt = _prompt()

    assert "Порядок тем" in prompt
    # The priority scale runs regulation → mass outages → individual questions.
    regulation = prompt.index("регулировани")
    outages = prompt.index("массовые сбои")
    private = prompt.index("частные вопросы")
    assert regulation < outages < private


def test_optional_elements_are_defined_with_when_to_omit():
    """«Что делать», «Сроки» and the quote of the day are optional, never forced."""
    prompt = _prompt()

    assert "👉 Что делать" in prompt
    assert "📅 Сроки" in prompt
    assert "Цитата дня" in prompt
    # Each optional element states when to leave it out.
    assert prompt.count("не добавляй") >= 3


def test_quiet_day_is_reported_honestly():
    """A thin day is said to be thin rather than padded into full themes."""
    prompt = _prompt()

    assert "Тихий день" in prompt


def test_existing_prohibitions_survive():
    """The Telegram-rendering rules that earlier fixes added must stay."""
    prompt = _prompt()

    assert "не используй таблицы" in prompt
    assert "Не добавляй в текст дату дайджеста" in prompt
    assert "Не добавляй собственный заголовок" in prompt
    assert "Не используй горизонтальные разделители" in prompt
    assert "Не пиши в дайджесте про спам" in prompt


def test_theme_heading_cites_the_first_message_of_its_discussion():
    """The model cites a reference; code, not the model, builds the URL."""
    prompt = _prompt()

    assert "[[m" in prompt
    assert "Не пиши ссылки" in prompt


def test_reply_threads_inform_status_and_grouping():
    """The model is told what the reply mark means and what to do with it."""
    prompt = _prompt()

    assert "↳" in prompt


def test_theme_heading_carries_industry_hashtags_from_the_input():
    prompt = _prompt()

    assert "Хэштеги" in prompt
    assert "#молоко" in prompt


def test_hottest_themes_are_flagged():
    prompt = _prompt()

    assert "🔥" in prompt


def test_previous_digest_is_reference_only():
    """ADR-0003: yesterday's Digest informs 🔁, it is never retold."""
    prompt = _prompt()

    assert "Справка: вчерашний дайджест" in prompt
    assert "не пересказывай" in prompt


def test_weekly_prompt_covers_trends_top_themes_and_open_questions():
    prompt = _load_prompt(WEEKLY_PROMPT_PATH)

    assert "Тренды" in prompt
    assert "Топ-3" in prompt
    assert "без ответа" in prompt
    assert "не используй таблицы" in prompt
    assert "Не добавляй собственный заголовок" in prompt


def test_kratko_is_a_list_not_one_long_line():
    """A dot-separated run-on line turns into a solid paragraph on a phone."""
    prompt = _prompt()

    assert "« · »" not in prompt
    assert "- тема одной строкой" in prompt
