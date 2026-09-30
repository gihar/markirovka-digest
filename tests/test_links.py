"""Behavior: a Monitored Chat message maps to its t.me link, or to nothing."""

from links import message_url


def test_public_chat_links_by_username():
    assert (
        message_url(-1001205001393, "markirovka_main", 185041)
        == "https://t.me/markirovka_main/185041"
    )


def test_private_supergroup_links_by_internal_id():
    # Supergroup ids are -100<internal>; t.me/c/ takes the internal part.
    assert message_url(-1001205001393, None, 7) == "https://t.me/c/1205001393/7"


def test_empty_username_counts_as_private():
    assert message_url(-1001205001393, "", 7) == "https://t.me/c/1205001393/7"


def test_basic_group_without_username_has_no_link():
    # Plain groups (no -100 prefix) have no t.me address at all.
    assert message_url(-4567, None, 7) is None


def test_unknown_message_id_has_no_link():
    assert message_url(-1001205001393, "markirovka_main", None) is None
