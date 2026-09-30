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

    ``posts`` are in publication order. The Digest starts at the post carrying
    its title — the latest one, if a rerun published it twice — and continues
    through the continuation parts that directly follow it in the same chat.
    Every other channel post (another day's Digest, third-party posts) is
    ignored.
    """
    title = digest_title(day)
    starts = [i for i, post in enumerate(posts) if post.text.startswith(title)]
    if not starts:
        return None

    first = posts[starts[-1]]
    parts = [first.text]
    for post in posts[starts[-1] + 1:]:
        if post.chat_id != first.chat_id:
            continue
        if not post.text.startswith(CONTINUATION_TEXT):
            break
        parts.append(post.text.removeprefix(CONTINUATION_TEXT).strip())
    return "\n\n".join(parts)
