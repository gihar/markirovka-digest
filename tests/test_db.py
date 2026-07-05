"""Behavior: fetch_digest_messages reads the previous MSK day from the Store."""

from datetime import UTC, date, datetime

from db import fetch_digest_messages

DAY = date(2026, 7, 4)


def _chat(conn, chat_id: int, title: str):
    conn.execute(
        "INSERT INTO chats (id, title) VALUES (%s, %s)", (chat_id, title)
    )


def _user(conn, user_id: int, first_name=None, username=None, is_bot=False):
    conn.execute(
        "INSERT INTO users (id, first_name, username, is_bot) VALUES (%s, %s, %s, %s)",
        (user_id, first_name, username, is_bot),
    )


def _msg(conn, chat_id, message_id, user_id, sent_at, text=None, caption=None):
    conn.execute(
        """INSERT INTO messages
           (message_id, chat_id, user_id, text, caption, sent_at)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (message_id, chat_id, user_id, text, caption, sent_at),
    )


def test_excludes_messages_from_bot_accounts(pg_conn):
    _chat(pg_conn, -1001, "Маркировка")
    _user(pg_conn, 9, username="spambot", is_bot=True)
    _user(pg_conn, 5, username="ivan")  # human (default is_bot=False)
    at = datetime(2026, 7, 4, 10, 0, tzinfo=UTC)
    _msg(pg_conn, -1001, 1, 9, at, text="реклама от бота, купи всё прямо сейчас")
    _msg(pg_conn, -1001, 2, 5, at, text="содержательное сообщение по маркировке")

    messages = fetch_digest_messages(pg_conn, [-1001], DAY, min_length=1)

    assert [m.text for m in messages] == ["содержательное сообщение по маркировке"]


def test_returns_a_message_sent_on_the_target_msk_day(pg_conn):
    _chat(pg_conn, -1001, "Маркировка. Главный чат")
    _user(pg_conn, 5, first_name="Иван", username="ivan")
    _msg(
        pg_conn, -1001, 1, 5,
        datetime(2026, 7, 4, 10, 0, tzinfo=UTC),  # 13:00 MSK, Jul 4
        text="Вопрос про коды маркировки на молоко",
    )

    messages = fetch_digest_messages(pg_conn, [-1001], DAY, min_length=1)

    assert len(messages) == 1
    m = messages[0]
    assert m.chat_id == -1001
    assert m.chat_title == "Маркировка. Главный чат"
    assert m.sender_name == "ivan"
    assert m.text == "Вопрос про коды маркировки на молоко"


def test_excludes_chats_outside_the_allow_list(pg_conn):
    _chat(pg_conn, -1001, "Маркировка")
    _chat(pg_conn, -2002, "Vibecoder")  # scraped, but not markirovka
    _user(pg_conn, 5, username="ivan")
    at = datetime(2026, 7, 4, 10, 0, tzinfo=UTC)
    _msg(pg_conn, -1001, 1, 5, at, text="про маркировку молока")
    _msg(pg_conn, -2002, 1, 5, at, text="про совсем другое")

    messages = fetch_digest_messages(pg_conn, [-1001], DAY, min_length=1)

    assert [m.chat_id for m in messages] == [-1001]


def test_respects_moscow_calendar_day_boundaries(pg_conn):
    _chat(pg_conn, -1001, "Маркировка")
    _user(pg_conn, 5, username="ivan")
    # In / out of the Jul 4 MSK day (MSK = UTC+3):
    _msg(pg_conn, -1001, 1, 5, datetime(2026, 7, 3, 21, 30, tzinfo=UTC),
         text="00:30 MSK Jul 4 — IN")
    _msg(pg_conn, -1001, 2, 5, datetime(2026, 7, 4, 20, 59, tzinfo=UTC),
         text="23:59 MSK Jul 4 — IN")
    _msg(pg_conn, -1001, 3, 5, datetime(2026, 7, 3, 20, 30, tzinfo=UTC),
         text="23:30 MSK Jul 3 — OUT")
    _msg(pg_conn, -1001, 4, 5, datetime(2026, 7, 4, 21, 0, tzinfo=UTC),
         text="00:00 MSK Jul 5 — OUT")

    messages = fetch_digest_messages(pg_conn, [-1001], DAY, min_length=1)

    assert sorted(m.text for m in messages) == [
        "00:30 MSK Jul 4 — IN",
        "23:59 MSK Jul 4 — IN",
    ]


def test_skips_messages_shorter_than_min_length(pg_conn):
    _chat(pg_conn, -1001, "Маркировка")
    _user(pg_conn, 5, username="ivan")
    at = datetime(2026, 7, 4, 10, 0, tzinfo=UTC)
    _msg(pg_conn, -1001, 1, 5, at, text="ок")  # 2 chars — noise
    _msg(pg_conn, -1001, 2, 5, at, text="это содержательное сообщение")

    messages = fetch_digest_messages(pg_conn, [-1001], DAY, min_length=10)

    assert [m.text for m in messages] == ["это содержательное сообщение"]


def test_uses_caption_when_text_is_null(pg_conn):
    _chat(pg_conn, -1001, "Маркировка")
    _user(pg_conn, 5, username="ivan")
    _msg(pg_conn, -1001, 1, 5, datetime(2026, 7, 4, 10, 0, tzinfo=UTC),
         text=None, caption="подпись к фото про этикетку")

    messages = fetch_digest_messages(pg_conn, [-1001], DAY, min_length=1)

    assert [m.text for m in messages] == ["подпись к фото про этикетку"]


def test_author_falls_back_first_name_then_unknown(pg_conn):
    _chat(pg_conn, -1001, "Маркировка")
    _user(pg_conn, 5, first_name="Пётр", username=None)  # no username
    at = datetime(2026, 7, 4, 10, 0, tzinfo=UTC)
    _msg(pg_conn, -1001, 1, 5, at, text="сообщение без username")
    _msg(pg_conn, -1001, 2, None, at, text="сообщение без автора")  # user_id NULL

    messages = fetch_digest_messages(pg_conn, [-1001], DAY, min_length=1)
    by_text = {m.text: m.sender_name for m in messages}

    assert by_text["сообщение без username"] == "Пётр"
    assert by_text["сообщение без автора"] == "Unknown"


def test_orders_by_chat_title_then_time(pg_conn):
    _chat(pg_conn, -1001, "Маркировка. Молоко")
    _chat(pg_conn, -1002, "Маркировка. Главный чат")
    _user(pg_conn, 5, username="ivan")
    _msg(pg_conn, -1001, 1, 5, datetime(2026, 7, 4, 12, 0, tzinfo=UTC), text="молоко второе")
    _msg(pg_conn, -1001, 2, 5, datetime(2026, 7, 4, 8, 0, tzinfo=UTC), text="молоко первое")
    _msg(pg_conn, -1002, 1, 5, datetime(2026, 7, 4, 9, 0, tzinfo=UTC), text="главный чат")

    messages = fetch_digest_messages(pg_conn, [-1001, -1002], DAY, min_length=1)

    # "Главный чат" sorts before "Молоко"; within a chat, by time ascending.
    assert [m.text for m in messages] == ["главный чат", "молоко первое", "молоко второе"]
