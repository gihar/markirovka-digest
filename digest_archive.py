"""Published Digests, read back from the Digest Channel's forwarded posts.

The Digest Service keeps no state of its own (ADR-0001): a Digest it published
earlier survives only as Digest Channel posts that Telegram forwarded into the
linked Monitored Chat. This module recognises one Digest among those posts and
rejoins its parts. What a Digest may do with the result is bounded by ADR-0003.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from render import CONTINUATION_TEXT, digest_title


@dataclass(frozen=True)
class ChannelPost:
    """A Digest Channel post as the Message Store holds it (forwarded)."""

    chat_id: int  # the Monitored Chat it was forwarded into
    text: str


def published_digest(posts: Sequence[ChannelPost], day: date) -> str | None:
    """The Digest covering ``day``, reassembled from ``posts``, or None.

    ``posts`` are in publication order. The Digest starts at a post carrying
    its title and continues through the continuation parts that directly
    follow it in the same chat. The title can appear more than once — a rerun
    publishes it again, and a reader may forward part one into another chat —
    so the most complete copy wins, and among equally complete ones the latest.
    Every other channel post (another day's Digest, third-party posts) is
    ignored.
    """
    title = digest_title(day)
    copies = [
        _assemble(posts, i)
        for i, post in enumerate(posts)
        if post.text.startswith(title)
    ]
    if not copies:
        return None
    # max() keeps the first of equals, so scan latest-first.
    return max(reversed(copies), key=len)


def _assemble(posts: Sequence[ChannelPost], start: int) -> str:
    """The Digest whose title post is ``posts[start]``, rejoined from its parts."""
    first = posts[start]
    parts = [first.text]
    for post in posts[start + 1:]:
        if post.chat_id != first.chat_id:
            continue
        if not post.text.startswith(CONTINUATION_TEXT):
            break
        parts.append(post.text.removeprefix(CONTINUATION_TEXT).strip())
    return "\n\n".join(parts)
