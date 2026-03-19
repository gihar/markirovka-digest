"""Pipeline orchestrator — thin entry point that wires modules together."""

import asyncio
from datetime import datetime, timedelta, timezone

from config import load_config
from db import init_db, fetch_messages_since
from downloader import download_messages
from analyzer import generate_digest
from publisher import publish


async def run_pipeline() -> None:
    """Execute the full digest pipeline: download, analyze, publish."""
    config = load_config()
    conn = init_db(config.db_path)

    try:
        # Step 1: Download new messages from Telegram channels
        new_count = await download_messages(config, conn)
        print(f"Downloaded {new_count} new messages")

        # Step 2: Fetch messages within the digest window
        since = datetime.now(timezone.utc) - timedelta(hours=config.digest_hours)
        messages = fetch_messages_since(conn, since)
        if not messages:
            print("No messages to digest")
            return

        # Step 3: Generate digest via Claude
        digest = generate_digest(messages, config.prompt_path)

        # Step 4: Publish to GitHub Issues and Telegram
        await publish(digest, config)

        print(f"Digest published: {digest.date} ({digest.message_count} messages)")
    finally:
        conn.close()


def run() -> None:
    """Synchronous entry point for CLI and CI usage."""
    asyncio.run(run_pipeline())


if __name__ == "__main__":
    run()
