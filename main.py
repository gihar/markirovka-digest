"""Pipeline orchestrator — read the Message Store, generate the Digest, post it.

Synchronous: the Digest Service is a read-only consumer (ADR-0001), so there is
no Telegram ingestion and no asyncio. Runs once (Railway Cron) and exits.
"""

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Callable

from analyzer import generate_digest
from config import Config, load_config
from db import connect, fetch_channel_posts, fetch_digest_messages
from digest_archive import published_digest
from models import DigestResult, TelegramMessage
from publisher import alert_degraded, alert_failure, publish
from window import previous_msk_day

logger = logging.getLogger(__name__)


def run_pipeline(
    day: date,
    prompt_path: Path,
    fetch_messages: Callable[[], list[TelegramMessage]],
    generate: Callable[[list[TelegramMessage], Path, str], DigestResult],
    publish_digest: Callable[[DigestResult], int],
    report_digest: Callable[[DigestResult], None],
) -> int | None:
    """Run the digest pipeline for ``day``.

    Returns the number of Telegram parts sent, or None if there were no messages
    to digest (nothing is published on a quiet day).

    ``report_digest`` sees the digest once it is published — it flags a run that
    a fallback provider served (ADR-0002), so it runs after publishing and never
    stands between the digest and its readers.
    """
    messages = fetch_messages()
    if not messages:
        logger.info("No messages for %s — nothing to publish", day)
        return None

    digest = generate(messages, prompt_path, day.isoformat())
    parts = publish_digest(digest)
    report_digest(digest)
    return parts


def run_and_alert_on_failure(
    work: Callable[[], int | None],
    alert: Callable[[BaseException], None],
) -> int | None:
    """Run ``work``, and if it fails, alert before letting the failure out.

    The failure keeps propagating: a run that could not publish must exit
    non-zero, or the cron platform records a success and the silence goes
    unnoticed — which is exactly how five days passed with nothing published.
    """
    try:
        return work()
    except Exception as error:
        alert(error)
        raise


def _previous_digest(conn, chat_ids: list[int], day: date, config: Config) -> str | None:
    """The Digest published for the day before ``day``, if there is one.

    Reference context only (ADR-0003). It was published on ``day`` itself, or
    later on a rerun, so posts from the day before on are enough to search.
    """
    yesterday = day - timedelta(days=1)
    posts = fetch_channel_posts(
        conn, chat_ids, since=yesterday, digest_channel_id=config.digest_channel_id
    )
    previous = published_digest(posts, yesterday)
    if previous is None:
        logger.info("No published Digest for %s — generating without it", yesterday)
    return previous


def _digest_once(day: date, config: Config) -> int | None:
    """Open the Message Store, run the pipeline for ``day``, close it again."""
    chat_ids = [c.chat_id for c in config.channels]
    chat_hashtags = {c.chat_id: c.hashtag for c in config.channels if c.hashtag}
    conn = connect(config.database_url)
    try:
        return run_pipeline(
            day=day,
            prompt_path=config.prompt_path,
            fetch_messages=lambda: fetch_digest_messages(
                conn,
                chat_ids,
                day,
                config.min_message_length,
                config.digest_channel_id,
            ),
            generate=lambda msgs, prompt_path, date_str: generate_digest(
                msgs,
                prompt_path,
                date_str,
                providers=config.llm_providers,
                chat_hashtags=chat_hashtags,
                previous_digest=_previous_digest(conn, chat_ids, day, config),
            ),
            publish_digest=lambda digest: publish(digest, config),
            report_digest=lambda digest: alert_degraded(day, digest, config),
        )
    finally:
        conn.close()


def run() -> None:
    """Wire real collaborators and run the pipeline once."""
    logging.basicConfig(level=logging.INFO)
    # Config load sits outside the alert guard by necessity: the alert is sent
    # with the bot token and chat id that load_config produces, so a failure
    # here has nothing to send with. Everything after it is covered.
    config: Config = load_config()
    day = previous_msk_day(datetime.now(tz=UTC))

    parts = run_and_alert_on_failure(
        lambda: _digest_once(day, config),
        lambda error: alert_failure(day, error, config),
    )

    if parts is not None:
        logger.info("Digest for %s published in %d Telegram message(s)", day, parts)


if __name__ == "__main__":
    run()
