"""Weekly review — the week's published Digests, condensed into one post.

A separate run-once entry point (Railway Cron, Fridays): it reads the Digests
published over the seven days ending yesterday back from the Message Store
(ADR-0003), asks the LLM for trends, the week's top themes and the questions
left open, and publishes the review to the Digest Channel. The daily Digest is
untouched.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

from analyzer import complete, keep_known_hashtags
from config import WEEKLY_PROMPT_PATH, Config, load_config
from db import connect, fetch_channel_posts
from digest_archive import ChannelPost, published_digest
from main import run_and_alert_on_failure
from models import DigestResult, LlmProvider
from publisher import alert_degraded, alert_failure, send_parts
from render import render_titled_parts
from window import previous_msk_week

logger = logging.getLogger(__name__)

# One day's Digest, as published.
DayDigest = tuple[date, str]


def _period(start: date, end: date) -> str:
    return f"{start.strftime('%d.%m')}–{end.strftime('%d.%m.%Y')}"


def weekly_title(start: date, end: date) -> str:
    """The review's bold title, naming the week it covers.

    Deliberately unlike the Digest title, so the archive never mistakes a
    review for a Digest (digest_archive.published_digest).
    """
    return f"📊 **Обзор недели по маркировке: {_period(start, end)}**"


def _subject(start: date, end: date) -> str:
    """How alerts name the review."""
    return f"Обзор недели {_period(start, end)}"


def week_digests(
    posts: Sequence[ChannelPost], start: date, end: date
) -> list[DayDigest]:
    """Each day's published Digest from ``start`` to ``end``, in date order.

    A day without a published Digest (quiet, or a failed run) is skipped.
    """
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    found = [(day, published_digest(posts, day)) for day in days]
    return [(day, text) for day, text in found if text is not None]


def weekly_input(digests: Sequence[DayDigest]) -> str:
    """The model's input: one section per day's Digest."""
    return "\n\n".join(
        f"## Дайджест за {day.strftime('%d.%m.%Y')}\n\n{text.strip()}"
        for day, text in digests
    )


def generate_weekly_review(
    digests: Sequence[DayDigest],
    prompt_path: Path,
    end: date,
    *,
    providers: tuple[LlmProvider, ...],
    chat_hashtags: Mapping[int, str] | None = None,
    post: Callable[[str, dict, dict], dict] | None = None,
) -> DigestResult:
    """Ask the LLM chain for the review of ``digests``.

    The result is a DigestResult dated by the week's last day, with
    ``message_count`` counting the Digests read. The Digests' own hashtags pass
    through the same closed-set filter as a daily Digest's.
    """
    input_md = weekly_input(digests)
    answer = complete(providers, prompt_path, input_md, post)
    allowed = frozenset((chat_hashtags or {}).values())
    return DigestResult(
        date=end.isoformat(),
        markdown=keep_known_hashtags(answer.markdown, allowed),
        message_count=len(digests),
        chat_count=0,
        token_count=len(input_md) // 4,
        model=answer.model,
        provider_failures=answer.skipped,
        links=MappingProxyType({}),
    )


def run_weekly_pipeline(
    fetch_digests: Callable[[], list[DayDigest]],
    generate: Callable[[list[DayDigest]], DigestResult],
    publish_review: Callable[[DigestResult], int],
    report_review: Callable[[DigestResult], None],
) -> int | None:
    """Build and publish the review. Returns parts sent, or None if no Digests.

    A week without a single published Digest has nothing to review, so nothing
    is posted — like a quiet day for the daily Digest.
    """
    digests = fetch_digests()
    if not digests:
        logger.info("No published Digests this week — no review")
        return None

    review = generate(digests)
    parts = publish_review(review)
    report_review(review)
    return parts


def _review_once(start: date, end: date, config: Config) -> int | None:
    """Open the Message Store, run the weekly pipeline, close it again."""
    chat_ids = [c.chat_id for c in config.channels]
    chat_hashtags = {c.chat_id: c.hashtag for c in config.channels if c.hashtag}
    conn = connect(config.database_url)
    try:
        return run_weekly_pipeline(
            fetch_digests=lambda: week_digests(
                fetch_channel_posts(
                    conn, chat_ids, since=start,
                    digest_channel_id=config.digest_channel_id,
                ),
                start,
                end,
            ),
            generate=lambda digests: generate_weekly_review(
                digests,
                WEEKLY_PROMPT_PATH,
                end,
                providers=config.llm_providers,
                chat_hashtags=chat_hashtags,
            ),
            publish_review=lambda review: send_parts(
                render_titled_parts(weekly_title(start, end), review.markdown),
                config.telegram_bot_token,
                config.telegram_digest_chat_id,
            ),
            report_review=lambda review: alert_degraded(
                end, review, config, subject=_subject(start, end)
            ),
        )
    finally:
        conn.close()


def run() -> None:
    """Wire real collaborators and publish the review for the week ending yesterday."""
    logging.basicConfig(level=logging.INFO)
    config: Config = load_config()
    start, end = previous_msk_week(datetime.now(tz=UTC))

    parts = run_and_alert_on_failure(
        lambda: _review_once(start, end, config),
        lambda error: alert_failure(
            end, error, config, subject=_subject(start, end)
        ),
    )

    if parts is not None:
        logger.info(
            "Weekly review %s published in %d Telegram message(s)",
            _period(start, end), parts,
        )


if __name__ == "__main__":
    run()
