"""The Digest Window: the previous calendar day in Europe/Moscow.

A pure helper, isolated from the database layer so it can be reasoned about and
tested without a connection.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")


def previous_msk_day(now: datetime) -> date:
    """Return the calendar date of the day *before* ``now`` in Europe/Moscow.

    ``now`` must be timezone-aware. The result is the MSK date that the Digest
    covers when the pipeline runs at ``now``.
    """
    return (now.astimezone(MSK) - timedelta(days=1)).date()


def previous_msk_week(now: datetime) -> tuple[date, date]:
    """Return (first, last) MSK dates of the seven days ending yesterday.

    The weekly review covers them: run on a Friday, it spans the previous
    Friday through Thursday, whose Digest was published that same morning.
    """
    last = previous_msk_day(now)
    return last - timedelta(days=6), last
