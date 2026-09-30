"""Telegram addresses of Monitored Chat messages.

A Digest links each theme back to the discussion it summarises. The model only
cites a message reference; the URL is built here from ids the Message Store
holds, so a link in the Digest can never point somewhere the model made up.
"""

# Supergroup and channel ids are "-100" followed by the internal id that
# t.me/c/ links take.
_SUPERGROUP_OFFSET: int = 10**12


def message_url(
    chat_id: int, chat_username: str | None, message_id: int | None
) -> str | None:
    """The t.me URL of a message, or None when Telegram gives it no address.

    Public chats link by username and open for anyone; private supergroups link
    through t.me/c/, which opens only for their members — still useful, since
    the Digest's readers are largely the chats' own members. Basic groups have
    no t.me address at all.
    """
    if message_id is None:
        return None
    if chat_username:
        return f"https://t.me/{chat_username}/{message_id}"
    if chat_id <= -_SUPERGROUP_OFFSET:
        return f"https://t.me/c/{-chat_id - _SUPERGROUP_OFFSET}/{message_id}"
    return None
