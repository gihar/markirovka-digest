"""Behavior: a published Digest is recognised and reassembled from channel posts."""

from datetime import date

from digest_archive import ChannelPost, published_digest

DAY = date(2026, 9, 29)
TITLE = "🗓 Дайджест чатов по маркировке за 29.09.2026"
MAIN = -1001205001393


def _post(text, chat_id=MAIN):
    return ChannelPost(chat_id=chat_id, text=text)


def test_a_single_part_digest_is_found_by_its_title():
    posts = [_post(f"{TITLE}\n5 чатов · 90 сообщений\n\nДень про сбои.")]

    assert published_digest(posts, DAY) == posts[0].text


def test_continuation_parts_are_joined_in_order():
    posts = [
        _post(f"{TITLE}\n\nЧасть один."),
        _post("(продолжение)\n\nЧасть два."),
        _post("(продолжение)\n\nЧасть три."),
    ]

    assert published_digest(posts, DAY) == (
        f"{TITLE}\n\nЧасть один.\n\nЧасть два.\n\nЧасть три."
    )


def test_other_channel_posts_are_never_read():
    posts = [
        _post("Мониторинг маркировки: 29.09.2026\n1. Важно…"),
        _post(f"{TITLE}\n\nДайджест."),
        _post("Мониторинг маркировки: 30.09.2026\n1. Важно…"),
    ]

    assert published_digest(posts, DAY) == f"{TITLE}\n\nДайджест."


def test_a_digest_of_another_day_does_not_count():
    posts = [_post("🗓 Дайджест чатов по маркировке за 28.09.2026\n\nВчера.")]

    assert published_digest(posts, DAY) is None


def test_a_rerun_digest_wins_over_the_earlier_post():
    posts = [
        _post(f"{TITLE}\n\nПервый прогон."),
        _post(f"{TITLE}\n\nПовторный прогон."),
    ]

    assert published_digest(posts, DAY) == f"{TITLE}\n\nПовторный прогон."


def test_a_continuation_from_another_chat_is_not_joined():
    posts = [
        _post(f"{TITLE}\n\nЧасть один."),
        _post("(продолжение)\n\nЧужая часть.", chat_id=-1009),
    ]

    assert published_digest(posts, DAY) == f"{TITLE}\n\nЧасть один."


def test_no_posts_means_no_digest():
    assert published_digest([], DAY) is None
