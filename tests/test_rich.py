"""Behavior: render a Digest to one Rich Message markdown (ADR-0004).

Rich-путь рендерит выпуск в markdown для sendRichMessage: настоящий заголовок
выпуска (h2, титульная строка — контракт архива из ADR-0003), темы h3,
свёрнутое тело, ссылки «обсуждение», футер. Старый HTML-путь (render.py) не
тронут и остаётся fallback'ом.
"""

import pytest

from models import DigestResult

from rich import flatten_rich_message, render_rich


def _digest(markdown: str, **overrides) -> DigestResult:
    payload = dict(
        date="2026-07-04",
        markdown=markdown,
        message_count=312,
        chat_count=5,
        token_count=1,
        model="anthropic/claude-sonnet-4.6",
        provider_failures=(),
    )
    payload.update(overrides)
    return DigestResult(**payload)


def test_rich_opens_with_title_heading_and_activity_line():
    out = render_rich(_digest("День спокойный: существенных обсуждений не было."))

    lines = out.split("\n")
    assert lines[0] == "##### 🗓 Дайджест чатов по маркировке за 04.07.2026"
    # Строка активности — абзацем сразу под заголовком, до резюме.
    assert lines[2] == "5 чатов · 312 сообщений"


def test_theme_heading_becomes_h3_with_link_and_folded_body():
    md = (
        "Резюме дня: одна тема.\n\n"
        "❓ **Ошибки в УПД при отгрузке** (Главный чат) [[m12]]\n"
        "Обсуждение показало, что ошибки растут при отгрузке без кодов.\n"
        "Участники сверили порядок заполнения.\n"
        "👉 Что делать: проверить коды в УПД до отгрузки.\n\n"
        "**Кратко**\n"
        "- мелкий вопрос одной строкой"
    )
    out = render_rich(_digest(md, links={"m12": "https://t.me/c/1/12"}))

    # Заголовок темы — настоящий h6: жирный из markdown снят, ссылка подставлена.
    assert (
        "###### ❓ Ошибки в УПД при отгрузке (Главный чат) "
        "[обсуждение](https://t.me/c/1/12)" in out
    )
    # «Что делать» — вне свёртки, отдельным абзацем: пустая строка перед ней,
    # иначе мягкий перенос склеивает её с заголовком темы.
    assert "\n\n👉 Что делать: проверить коды в УПД до отгрузки.\n\n" in out
    # Тело темы — отдельным свёрнутым абзацем.
    assert "\n\n<blockquote expandable>" in out
    folded = out.split("<blockquote expandable>")[1].split("</blockquote>")[0]
    assert "Обсуждение показало" in folded
    assert "сверили порядок" in folded
    # «Кратко» — жирный абзац, без превращения в заголовок.
    assert "**Кратко**" in out


def test_unknown_reference_is_dropped_silently():
    out = render_rich(_digest("❓ **Тема без ссылки** [[m99]]\nтело темы"))

    assert "[[m99]]" not in out
    assert "обсуждение" not in out


def test_rich_ends_with_footer_naming_the_issue():
    out = render_rich(_digest("Резюме дня."))

    assert out.endswith("<footer>Дайджест чатов по маркировке · 04.07.2026</footer>")


def test_limits_reject_text_over_the_rich_message_budget():
    from rich import validate_rich_limits

    with pytest.raises(ValueError, match="32 ?768"):
        validate_rich_limits("x" * 32_769)


def test_limits_reject_block_count_over_budget():
    from rich import validate_rich_limits

    many_items = "\n".join(f"- пункт {i}" for i in range(600))
    with pytest.raises(ValueError, match="500"):
        validate_rich_limits(many_items)


def test_limits_accept_a_normal_digest():
    from rich import render_rich, validate_rich_limits

    validate_rich_limits(render_rich(_digest("Резюме.\n\n❓ **Тема** [[m1]]\nтело\n👉 Что делать: шаг.")))


# --- flatten: чтение rich-постов из Message Store (#23) ---

# Реальные receive-формы блоков Telegram: сняты getUpdates-экспериментом
# 2026-11-06 с пересланного rich-сообщения (heading.text — строка,
# paragraph.text — RichText, list.items[].blocks, details.summary/blocks,
# footer.text — строка).
_RICH_RAW = {
    "message_id": 2,
    "rich_message": {
        "blocks": [
            {"type": "heading", "text": "🗓 Дайджест чатов по маркировке за 04.07.2026", "size": 5},
            {"type": "paragraph", "text": ["Проверка ", {"type": "code", "text": "sendRichMessage"}, " в деле."]},
            {"type": "heading", "text": "Тема дня", "size": 6},
            {"type": "paragraph", "text": "Суть обсуждения одной строкой."},
            {"type": "list", "items": [
                {"label": "•", "blocks": [{"type": "paragraph", "text": "первый пункт"}]},
                {"label": "•", "blocks": [{"type": "paragraph", "text": ["второй ", {"type": "bold", "text": "пункт"}]}]},
            ]},
            {"type": "blockquote", "blocks": [{"type": "paragraph", "text": "свёрнутое тело темы"}]},
            {"type": "details", "summary": "Все ссылки выпуска (3)", "blocks": [
                {"type": "list", "items": [
                    {"label": "1.", "blocks": [{"type": "paragraph", "text": "ссылка раз"}]},
                ]},
            ]},
            {"type": "footer", "text": "Дайджест чатов по маркировке · 04.07.2026"},
        ]
    },
}


def test_flatten_turns_a_received_rich_message_back_into_text():
    text = flatten_rich_message(_RICH_RAW)

    paragraphs = text.split("\n\n")
    # Первая строка — титульная, ровно как требует контракт ADR-0003.
    assert paragraphs[0] == "🗓 Дайджест чатов по маркировке за 04.07.2026"
    # RichText-прогоны склеиваются в одну строку абзаца.
    assert "Проверка sendRichMessage в деле." in paragraphs
    # Список — построчно, с меткой пункта.
    assert "• первый пункт" in paragraphs
    assert "• второй пункт" in paragraphs
    # Свёрнутое тело темы не теряется.
    assert "свёрнутое тело темы" in paragraphs
    # Details: сначала summary, потом содержимое.
    assert "Все ссылки выпуска (3)" in paragraphs
    assert "1. ссылка раз" in paragraphs
    assert paragraphs[-1] == "Дайджест чатов по маркировке · 04.07.2026"


def test_flatten_returns_none_without_rich_message():
    assert flatten_rich_message(None) is None
    assert flatten_rich_message({"text": "обычное текстовое сообщение"}) is None
    assert flatten_rich_message({"rich_message": {"blocks": []}}) is None


def test_flatten_keeps_text_of_unknown_block_types():
    # Future block types must not silently lose content: whatever carries
    # text lands in the flattened output; what carries nothing is skipped.
    raw = {"rich_message": {"blocks": [
        {"type": "hologram", "text": "неизвестный, но текстовый"},
        {"type": "pin", "coordinates": [1, 2]},
        {"type": "paragraph", "text": "обычный абзац"},
    ]}}
    text = flatten_rich_message(raw)

    assert "неизвестный, но текстовый" in text
    assert "обычный абзац" in text
