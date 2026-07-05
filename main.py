"""Pipeline orchestrator — read the Message Store, generate the Digest, post it.

Synchronous: the Digest Service is a read-only consumer (ADR-0001), so there is
no Telegram ingestion and no asyncio. Runs once (Railway Cron) and exits.
"""

import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Callable

from analyzer import generate_digest
from config import Config, load_config
from db import connect, fetch_digest_messages
from models import DigestResult, TelegramMessage
from publisher import publish
from window import previous_msk_day

logger = logging.getLogger(__name__)


def run_pipeline(
    day: date,
    prompt_path: Path,
    fetch_messages: Callable[[], list[TelegramMessage]],
    generate: Callable[[list[TelegramMessage], Path, str], DigestResult],
    publish_digest: Callable[[DigestResult], int],
) -> int | None:
    """Run the digest pipeline for ``day``.

    Returns the number of Telegram parts sent, or None if there were no messages
    to digest (nothing is published on a quiet day).
    """
    messages = fetch_messages()
    if not messages:
        logger.info("No messages for %s — nothing to publish", day)
        return None

    digest = generate(messages, prompt_path, day.isoformat())
    return publish_digest(digest)


def run() -> None:
    """Wire real collaborators and run the pipeline once."""
    logging.basicConfig(level=logging.INFO)
    config: Config = load_config()
    day = previous_msk_day(datetime.now(tz=UTC))
    chat_ids = [c.chat_id for c in config.channels]

    conn = connect(config.database_url)
    try:
        parts = run_pipeline(
            day=day,
            prompt_path=config.prompt_path,
            fetch_messages=lambda: fetch_digest_messages(
                conn, chat_ids, day, config.min_message_length
            ),
            generate=lambda msgs, prompt_path, date_str: generate_digest(
                msgs,
                prompt_path,
                date_str,
                base_url=config.llm_base_url,
                api_key=config.llm_api_key,
                model=config.llm_model,
            ),
            publish_digest=lambda digest: publish(digest, config),
        )
    finally:
        conn.close()

    if parts is not None:
        logger.info("Digest for %s published in %d Telegram message(s)", day, parts)


if __name__ == "__main__":
    run()
