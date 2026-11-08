"""Render a Digest to a single Rich Message (Bot API ``sendRichMessage``).

The rich path of ADR-0004: the model's Markdown becomes the ``markdown``
field of one InputRichMessage — a real section heading for the issue, real
``h6`` headings for themes, theme bodies folded in expandable quotes — while
the legacy HTML path in render.py stays untouched as the delivery fallback.

Pure functions: sending lives in publisher.py. Reuses render.py's tested
transform helpers (strip HR, tighten themes, title, activity line, theme and
action-line patterns) so the two paths cannot drift on the model-markdown
contract.
"""

import re
from collections.abc import Mapping
from datetime import date

from models import DigestResult
from render import (
    _ACTION_LINE,
    _REFERENCE,
    _THEME_HEADING,
    _activity_line,
    _strip_horizontal_rules,
    _tighten_themes,
    digest_title,
)

# A theme heading under h3 keeps its own emphasis, so the model's bold
# markers on the title come off; the status emoji stays as plain text.
_BOLD = re.compile(r"\*\*")

# LLMs habitually backslash-escape markdown punctuation in usernames and
# names (Alla\_tj1). Rich Markdown renders the backslash literally, so
# escapes of characters this renderer never treats as markup come off
# ('*' stays significant) — same rule as the legacy path in render.py.
_ESCAPE = re.compile(r"\\([_\[\]()~`#+\-=|{}.!])")

# An industry hashtag in a theme heading (with the space before it), moved
# to the end of the line so it cannot fuse with the discussion link.
_HASHTAG = re.compile(r"\s?(#[^\W\d_]\w*)")


def _strip_escapes(md: str) -> str:
    return _ESCAPE.sub(r"\1", md)

# Bot API limits for one InputRichMessage: 32 768 UTF-8 characters and 500
# blocks (list items, table rows and quote content each count as blocks).
MAX_RICH_CHARS: int = 32_768
MAX_RICH_BLOCKS: int = 500

_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+\.)\s")


def _estimate_blocks(md: str) -> int:
    """Count blocks the way Telegram will: every paragraph, heading or theme
    block is one, every list item one more, a quote adds its own. An estimate
    with margin — the exact server count is only known after parsing."""
    count = 0
    for paragraph in md.split("\n\n"):
        count += 1
        count += sum(1 for line in paragraph.split("\n") if _LIST_ITEM.match(line))
        if "<blockquote" in paragraph or "<details" in paragraph:
            count += 1
    return count


def validate_rich_limits(md: str) -> None:
    """Reject a rich message that cannot fit one InputRichMessage.

    Raises ValueError before anything is sent: the caller treats it as a
    definite failure (no request was made), not an unknown outcome.
    """
    chars = len(md)
    if chars > MAX_RICH_CHARS:
        raise ValueError(
            f"Rich message too long: {chars} characters, "
            f"the limit is {MAX_RICH_CHARS}."
        )
    blocks = _estimate_blocks(md)
    if blocks > MAX_RICH_BLOCKS:
        raise ValueError(
            f"Too many blocks: ~{blocks} estimated, the limit is {MAX_RICH_BLOCKS}."
        )


# ── Flatten: read a published rich post back as text (#23) ──────────────────
#
# A forwarded rich message carries no ``text`` field — its content lives in
# ``rich_message.blocks`` of the raw serialization the Scraper stores. The
# archive (weekly review, Previous Digest) needs plain text, so this turns
# received blocks back into paragraphs. Block shapes below are the ones
# Telegram actually returns (verified against a live forward, 2026-11-06).


def _richtext(value: object) -> str:
    """Concatenate a RichText node: a string, a list of runs, or an inline
    object carrying its own ``text``."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_richtext(part) for part in value)
    if isinstance(value, dict):
        return _richtext(value.get("text"))
    return ""


def _flatten_blocks(blocks: object) -> list[str]:
    """Paragraph lines for a list of received rich blocks.

    Known structural blocks (list, details) get shape-aware treatment;
    everything else contributes its ``text`` runs and recurses into nested
    ``blocks`` when it has them (blockquote, unknown future types). A block
    carrying neither is skipped — nothing to read from it.
    """
    if not isinstance(blocks, list):
        return []
    lines: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "list":
            for item in block.get("items", []):
                if not isinstance(item, dict):
                    continue
                inner = _flatten_blocks(item.get("blocks"))
                label = item.get("label") or "•"
                lines.append(f"{label} {' '.join(inner)}".rstrip())
        elif kind == "details":
            summary = _richtext(block.get("summary"))
            if summary:
                lines.append(summary)
            lines.extend(_flatten_blocks(block.get("blocks")))
        else:
            text = _richtext(block.get("text"))
            if text:
                lines.append(text)
            lines.extend(_flatten_blocks(block.get("blocks")))
    return lines


def flatten_rich_message(raw_message: object) -> str | None:
    """The plain text of a rich message's raw serialization, or None.

    None means "not a rich message": ordinary posts keep their own text and
    never pass through here.
    """
    rich = (
        raw_message.get("rich_message")
        if isinstance(raw_message, dict)
        else None
    )
    if not isinstance(rich, dict):
        return None
    paragraphs = [p for p in _flatten_blocks(rich.get("blocks")) if p]
    return "\n\n".join(paragraphs) if paragraphs else None


def _link_references(text: str, links: Mapping[str, str]) -> str:
    """Turn cited message references into markdown discussion links.

    Only a reference found in ``links`` — built from Message Store ids —
    becomes a link; an unknown one is dropped rather than published broken.
    No URL in the Digest ever comes from the model.
    """

    def link(match: re.Match) -> str:
        url = links.get(match.group(1))
        if url is None:
            return ""
        return f" [обсуждение]({url})"

    return _REFERENCE.sub(link, text)


def _rich_themes(md: str, links: Mapping[str, str]) -> str:
    """Promote theme headings to h3 and fold theme bodies, theme by theme.

    Same reader UX as the legacy path (_fold_themes): the heading and the
    «👉 Что делать» line stay visible, the body sits in one expandable quote.
    Non-theme paragraphs pass through with references linked.
    """
    out: list[str] = []
    for paragraph in md.split("\n\n"):
        lines = paragraph.split("\n")
        if not _THEME_HEADING.match(lines[0]):
            out.append(_link_references(paragraph, links))
            continue
        # Reorder: <emoji> Title (chats) [обсуждение](url) #hashtag — the
        # link follows the chat parens, hashtags trail, so two adjacent blue
        # links never fuse into one unreadable run.
        raw_heading = _BOLD.sub("", lines[0])
        hashtags = _HASHTAG.findall(raw_heading)
        raw_heading = _HASHTAG.sub("", raw_heading).rstrip()
        heading = _link_references(raw_heading, links).strip()
        if hashtags:
            heading = f"{heading} {' '.join(hashtags)}"
        heading = f"###### {heading}"
        actions = [ln for ln in lines[1:] if _ACTION_LINE.match(ln)]
        body = [ln for ln in lines[1:] if not _ACTION_LINE.match(ln)]
        # Каждый элемент — отдельным абзацем: в Rich Markdown мягкий перенос
        # строки внутри абзаца склеивается, и «👉 Что делать» без пустой
        # строки перед ней прилипает к заголовку темы.
        block = [heading, *actions]
        if body:
            block.append(
                f"<blockquote expandable>{chr(10).join(body)}</blockquote>"
            )
        out.append("\n\n".join(block))
    return "\n\n".join(out)


def render_rich_titled(
    title: str,
    markdown: str,
    links: Mapping[str, str] | None = None,
    *,
    footer: str = "",
    lead: str | None = None,
) -> str:
    """Render a titled issue to Rich Markdown: an ``h5`` heading (the title's
    own bold markers come off — the heading keeps its emphasis), an optional
    lead paragraph right under it, the transformed body, and a footer line.

    ``render_rich`` covers the daily Digest; the weekly review composes its
    own title and footer through this entry point.
    """
    heading = f"##### {_BOLD.sub('', title).strip()}"
    head = heading if lead is None else f"{heading}\n\n{lead}"
    body = _rich_themes(
        _tighten_themes(_strip_horizontal_rules(_strip_escapes(markdown))),
        links or {},
    )
    return f"{head}\n\n{body}\n\n<footer>{footer}</footer>"


def render_rich(digest: DigestResult) -> str:
    """Render a Digest to Rich Markdown for ``sendRichMessage``.

    The first line is the digest title under an ``h5`` heading — business
    sizing, per reader feedback (h2/h3 render display-size in Telegram);
    flattened back to plain text it equals ``digest_title`` exactly, which is
    the archive's recognition contract (ADR-0003).
    """
    day = date.fromisoformat(digest.date)
    return render_rich_titled(
        digest_title(day),
        digest.markdown,
        digest.links,
        footer="Дайджест чатов по маркировке",
        lead=_activity_line(digest),
    )
