"""Behavior: the Digest Window is the previous calendar day in Europe/Moscow."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from window import previous_msk_day

MSK = ZoneInfo("Europe/Moscow")


def test_afternoon_run_covers_yesterday():
    # 2026-07-05 09:00 MSK — the morning cron run.
    now = datetime(2026, 7, 5, 9, 0, tzinfo=MSK)
    assert previous_msk_day(now) == date(2026, 7, 4)


def test_just_after_midnight_msk_still_covers_yesterday():
    now = datetime(2026, 7, 5, 0, 30, tzinfo=MSK)
    assert previous_msk_day(now) == date(2026, 7, 4)


def test_utc_input_is_converted_to_msk_before_bucketing():
    # 2026-07-05 22:00 UTC == 2026-07-06 01:00 MSK -> previous MSK day is 07-05.
    now = datetime(2026, 7, 5, 22, 0, tzinfo=UTC)
    assert previous_msk_day(now) == date(2026, 7, 5)


def test_month_boundary():
    now = datetime(2026, 3, 1, 8, 0, tzinfo=MSK)
    assert previous_msk_day(now) == date(2026, 2, 28)


def test_production_passes_utc_cron_time():
    # main.py passes datetime.now(tz=UTC); 06:00 UTC == 09:00 MSK.
    now = datetime(2026, 7, 5, 6, 0, tzinfo=UTC)
    assert previous_msk_day(now) == date(2026, 7, 4)
