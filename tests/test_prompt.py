"""Content contract for the digest system prompt.

Locks the redesign where a THEME is the only structural unit of the digest and a
theme's status is an emoji attribute of its heading — not a separate section.
The prompt is loaded exactly as production does (``config.PROMPT_PATH`` through
``analyzer._load_prompt``) so these tests exercise the file that actually ships.
"""

import re

from analyzer import _load_prompt
from config import PROMPT_PATH


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
