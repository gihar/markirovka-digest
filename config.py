"""Configuration loading and validation. Single source of truth.

Loads secrets from environment variables and channel list from channels.toml.
Fails fast with clear messages on missing or invalid configuration.
"""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from models import ChannelConfig


# Project root is the directory containing this file
PROJECT_ROOT: Path = Path(__file__).resolve().parent
DB_PATH: Path = PROJECT_ROOT / "data" / "messages.db"
PROMPT_PATH: Path = PROJECT_ROOT / "prompts" / "digest.md"
CHANNELS_PATH: Path = PROJECT_ROOT / "channels.toml"

# Claude token limit before switching to map-reduce overflow path
MAX_CONTEXT_TOKENS: int = 120_000


@dataclass(frozen=True)
class Config:
    """Immutable application configuration."""

    telegram_api_id: int
    telegram_api_hash: str
    telegram_session: str
    anthropic_api_key: str
    telegram_bot_token: str
    telegram_digest_chat_id: str
    github_token: str
    github_repository: str
    channels: tuple[ChannelConfig, ...]
    db_path: Path
    prompt_path: Path
    digest_hours: int
    min_message_length: int


def _load_channels(path: Path) -> tuple[tuple[ChannelConfig, ...], dict]:
    """Parse channels.toml and return (channels, settings)."""
    if not path.exists():
        raise FileNotFoundError(f"Channel config not found: {path}")

    with open(path, "rb") as f:
        data = tomllib.load(f)

    settings = data.get("settings", {})

    raw_channels = data.get("channels")
    if not raw_channels:
        raise ValueError(f"No [[channels]] entries found in {path}")

    channels: list[ChannelConfig] = []
    for i, ch in enumerate(raw_channels):
        chat_id = ch.get("chat_id")
        title = ch.get("title")
        if chat_id is None or title is None:
            raise ValueError(
                f"Channel entry {i} in {path} missing 'chat_id' or 'title'"
            )
        channels.append(ChannelConfig(chat_id=int(chat_id), title=str(title)))

    return tuple(channels), settings


def _require_env(name: str) -> str:
    """Return an environment variable's value or raise with a clear message."""
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def load_config() -> Config:
    """Load and validate all configuration. Fail fast on missing values."""
    # Required env vars — fail immediately if any are missing
    required = [
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_SESSION",
        "ANTHROPIC_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_DIGEST_CHAT_ID",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    # Optional env vars with defaults for GitHub Actions context
    github_token = os.environ.get("GITHUB_TOKEN", "")
    github_repository = os.environ.get("GITHUB_REPOSITORY", "")

    channels, settings = _load_channels(CHANNELS_PATH)

    return Config(
        telegram_api_id=int(_require_env("TELEGRAM_API_ID")),
        telegram_api_hash=_require_env("TELEGRAM_API_HASH"),
        telegram_session=_require_env("TELEGRAM_SESSION"),
        anthropic_api_key=_require_env("ANTHROPIC_API_KEY"),
        telegram_bot_token=_require_env("TELEGRAM_BOT_TOKEN"),
        telegram_digest_chat_id=_require_env("TELEGRAM_DIGEST_CHAT_ID"),
        github_token=github_token,
        github_repository=github_repository,
        channels=channels,
        db_path=DB_PATH,
        prompt_path=PROMPT_PATH,
        digest_hours=settings.get("digest_hours", 25),
        min_message_length=settings.get("min_message_length", 30),
    )
