"""Render a Digest to Telegram HTML message parts.

Markdown from the model becomes Telegram's HTML subset, headed by the covered
day and its activity, and split into parts that each fit one Telegram message.
Pure functions — sending lives in publisher.py.
"""

import html
import re
from collections.abc import Mapping
from datetime import date
from typing import Callable

from models import DigestResult

# Telegram's hard limit for a single message.
TELEGRAM_LIMIT: int = 4096

# Prefix marking every part after the first as a continuation of the digest,
# so readers of a split digest know parts 2+ are not a fresh message. Rendered
# to <i>(продолжение)</i> followed by a blank line. As published, a part reads
# CONTINUATION_TEXT first — which is how the archive rejoins parts (ADR-0003).
CONTINUATION_TEXT: str = "(продолжение)"
_CONTINUATION_MARKER: str = f"*{CONTINUATION_TEXT}*\n\n"


# A message reference the model cites, e.g. "[[m12]]" — with the space before
# it, so a dropped reference leaves no trailing blank behind.
_REFERENCE = re.compile(r" ?\[\[(m\d+)\]\]")

_LINK_TEXT: str = "→ обсуждение"


def _link_references(text: str, links: Mapping[str, str]) -> str:
    """Turn cited message references into discussion links.

    Runs on already-escaped HTML. Only a reference found in ``links`` — built
    from Message Store ids — becomes a link; an unknown one is dropped rather
    than published broken. Markdown links the model writes itself are never
    converted, so no URL in the Digest comes from the model.
    """
    def link(match: re.Match) -> str:
        url = links.get(match.group(1))
        if url is None:
            return ""
        return f' <a href="{html.escape(url)}">{_LINK_TEXT}</a>'

    return _REFERENCE.sub(link, text)


def markdown_to_telegram_html(md: str, links: Mapping[str, str] | None = None) -> str:
    """Convert markdown to Telegram-compatible HTML.

    Escapes HTML entities first, then converts headers and bold/italic, then
    cited message references (see _link_references). Bold and italic stay
    within a single line, so line-boundary splitting never cuts a tag unless one
    line alone exceeds the Telegram limit.
    """
    text = html.escape(md)
    # Headers (## Header) must be handled before bold.
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    # Bold **text** before italic *text*.
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    # LLMs habitually backslash-escape markdown punctuation (tasy\_emdina).
    # Telegram HTML renders the backslash literally, so strip escapes of
    # characters this converter never treats as markup ('*' stays significant).
    text = re.sub(r"\\([_\[\]()~`#+\-=|{}.!])", r"\1", text)
    return _link_references(text, links or {})


def _largest_prefix(line: str, limit: int, measure: Callable[[str], int]) -> int:
    """Return the largest k>=1 such that ``measure(line[:k]) <= limit``.

    Binary search. Always returns at least 1 so hard-splitting makes progress
    even if a single unit already exceeds the limit.
    """
    lo, hi, best = 1, len(line), 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if measure(line[:mid]) <= limit:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _split_lines(
    text: str, limit: int, measure: Callable[[str], int]
) -> list[str]:
    """Split on line boundaries; a single line still too long is hard-split."""
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        # A single line that is itself too long gets hard-split.
        while measure(line) > limit:
            if current:
                parts.append(current)
                current = ""
            k = _largest_prefix(line, limit, measure)
            parts.append(line[:k])
            line = line[k:]

        candidate = line if not current else f"{current}\n{line}"
        if measure(candidate) <= limit:
            current = candidate
        else:
            if current:
                parts.append(current)
            current = line

    if current:
        parts.append(current)
    return parts


def split_message(
    text: str,
    limit: int = TELEGRAM_LIMIT,
    *,
    measure: Callable[[str], int] = len,
) -> list[str]:
    """Split text into parts each with ``measure(part) <= limit``, losslessly.

    Splits between paragraphs (blank-line separated) first, so a theme block
    stays whole in one part; only a paragraph too long for any part is split on
    line boundaries, and a single line still too long is hard-split.
    ``measure`` lets callers size by the *rendered* length (e.g. HTML) while
    splitting the raw source, so a split never lands inside a produced tag or
    entity. No non-newline character is dropped or duplicated; order is kept.
    """
    if measure(text) <= limit:
        return [text]

    parts: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if measure(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        if measure(paragraph) <= limit:
            current = paragraph
        else:
            *whole, current = _split_lines(paragraph, limit, measure)
            parts.extend(whole)

    if current:
        parts.append(current)
    return parts


# A theme heading opens with its status emoji (see the digest prompt).
_THEME_HEADING = re.compile(r"^\s*(✅|❓|🔁)")


def _fold_themes(html_text: str) -> str:
    """Fold each theme's body into an expandable quote under its heading.

    The heading stays visible in the feed and the body opens on tap. A body is
    the run of non-blank lines after a heading; summary, deadlines, the quote
    of the day and «Кратко» have no status heading and stay unfolded. Works on
    one rendered part at a time, so every part's tags are balanced even when a
    split lands inside a theme (the tail then shows unfolded).
    """
    lines = html_text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        if not _THEME_HEADING.match(line):
            continue
        end = i
        while end < len(lines) and lines[end].strip():
            end += 1
        if end > i:
            body = "\n".join(lines[i:end])
            out.append(f"<blockquote expandable>{body}</blockquote>")
        i = end
    return "\n".join(out)


def _title_text(day: date) -> str:
    return f"Дайджест чатов по маркировке за {day.strftime('%d.%m.%Y')}"


def digest_title(day: date) -> str:
    """The Digest's title line as readers see it — plain text, no markup.

    The archive recognises a published Digest by it (ADR-0003), so it and the
    rendered header below come from one place.
    """
    return f"🗓 {_title_text(day)}"


def _dated_header(date_str: str) -> str:
    """A bold, human-friendly digest title from an ISO date (→ DD.MM.YYYY).

    Injected by us — the single title of the message — rather than trusting the
    model to state the covered day (the prompt forbids the model its own title).
    """
    return f"🗓 **{_title_text(date.fromisoformat(date_str))}**"


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Russian plural form for ``n``: 1 чат, 3 чата, 5 чатов, 21 чат, 111 чатов."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _activity_line(digest: DigestResult) -> str | None:
    """"5 чатов · 312 сообщений" — how busy the covered day was.

    Counted by us from the digest's actual input, never by the model. None for
    a day with nothing in it: a line of zeros tells the reader nothing.
    """
    if digest.message_count == 0:
        return None
    chats = digest.chat_count
    messages = digest.message_count
    return (
        f"{chats} {_plural(chats, 'чат', 'чата', 'чатов')} · "
        f"{messages} {_plural(messages, 'сообщение', 'сообщения', 'сообщений')}"
    )


# A whole line that is only a Markdown horizontal rule (---, ***, ___, - - -).
_HR_LINE = re.compile(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$", re.MULTILINE)


def _strip_horizontal_rules(md: str) -> str:
    """Drop horizontal-rule lines; Telegram shows them as literal '---' clutter.

    Sections stay separated by the surrounding blank lines (runs collapsed).
    """
    md = _HR_LINE.sub("", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def render_parts(digest: DigestResult, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Render a Digest to ready-to-send Telegram HTML message parts.

    Heads it with the dated header (which day this digest covers) and the day's
    activity line; see render_titled_parts for the rest.
    """
    title = "\n".join(
        line for line in (_dated_header(digest.date), _activity_line(digest)) if line
    )
    return render_titled_parts(title, digest.markdown, digest.links, limit)


def render_titled_parts(
    title: str,
    markdown: str,
    links: Mapping[str, str] | None = None,
    limit: int = TELEGRAM_LIMIT,
) -> list[str]:
    """Render ``title`` over ``markdown`` as Telegram HTML message parts.

    Strips horizontal-rule lines, then splits the raw markdown — sizing each
    chunk by its rendered HTML length — and converts each chunk to HTML
    independently, folding theme bodies (_fold_themes). The title lands on the
    first part only. Splitting on the raw source guarantees a split never
    severs an HTML tag or entity, so every part is valid under parse_mode=HTML.
    (In the rare case an oversized single line is hard-split mid-``**bold**``,
    the orphaned markers render as literal asterisks — content is preserved
    either way.)

    When the text spans more than one message, every part after the first is
    prefixed with a continuation marker. The marker's rendered length is
    reserved during splitting (parts are sized to ``limit`` minus the marker),
    so a part plus its marker never exceeds the limit. Text that fits in a
    single message gets no marker and is unchanged.
    """
    def to_html(chunk: str) -> str:
        return _fold_themes(markdown_to_telegram_html(chunk, links))

    def html_len(chunk: str) -> int:
        return len(to_html(chunk))

    body = f"{title}\n\n{_strip_horizontal_rules(markdown)}"

    # Text that fits whole stays a single, marker-free message.
    if html_len(body) <= limit:
        return [to_html(body)]

    # It splits: reserve room for the marker on every part but the first. The
    # marker sits behind an inert blank line, so its rendered length adds to a
    # chunk's rendered length exactly — reducing the limit by it is precise.
    marker_len = html_len(_CONTINUATION_MARKER)
    raw_parts = split_message(body, limit - marker_len, measure=html_len)
    return [
        to_html(part if i == 0 else _CONTINUATION_MARKER + part)
        for i, part in enumerate(raw_parts)
    ]
