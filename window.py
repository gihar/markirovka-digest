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
