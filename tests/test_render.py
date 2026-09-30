"""Behavior: render a Digest to Telegram HTML parts, splitting long text losslessly."""

import pytest

from models import DigestResult
from render import markdown_to_telegram_html, render_parts, split_message


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


def test_a_theme_heading_keeps_its_hashtags_as_plain_text():
    # Telegram makes a bare "#молоко" clickable by itself; no markup needed.
    out = markdown_to_telegram_html("✅ **Тема** (Молоко) #молоко #легпром")
    assert out == "✅ <b>Тема</b> (Молоко) #молоко #легпром"


def test_render_parts_prepends_dated_header():
    parts = render_parts(_digest("**Итоги** дня"))
    assert parts[0].startswith("🗓 <b>Дайджест чатов по маркировке за 04.07.2026</b>")
    assert "<b>Итоги</b> дня" in parts[0]


def test_render_parts_short_digest_is_one_html_part():
    parts = render_parts(_digest("**Итоги** дня"), limit=4096)
    assert parts == [
        "🗓 <b>Дайджест чатов по маркировке за 04.07.2026</b>\n"
        "1 чат · 1 сообщение\n\n<b>Итоги</b> дня"
    ]


def _counted(message_count: int, chat_count: int) -> DigestResult:
    return DigestResult(
        **{**_digest("Итоги").__dict__,
           "message_count": message_count, "chat_count": chat_count}
    )


@pytest.mark.parametrize(
    "messages, chats, line",
    [
        (312, 5, "5 чатов · 312 сообщений"),
        (21, 1, "1 чат · 21 сообщение"),
        (24, 3, "3 чата · 24 сообщения"),
        (111, 2, "2 чата · 111 сообщений"),
        (14, 22, "22 чата · 14 сообщений"),
    ],
)
def test_activity_line_counts_chats_and_messages(messages, chats, line):
    [part] = render_parts(_counted(messages, chats))
    assert part.split("\n")[1] == line


def test_a_day_with_no_messages_gets_no_activity_line():
    [part] = render_parts(_counted(0, 0))
    assert "сообщени" not in part


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

    header = "🗓 <b>Дайджест чатов по маркировке за 04.07.2026</b>\n1 чат · 1 сообщение"
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
