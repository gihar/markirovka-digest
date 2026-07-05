"""Configuration loading and validation. Single source of truth.

Loads secrets from environment variables and the chat allow-list from
channels.toml. Fails fast with clear messages on missing or invalid config.

The Digest Service reads from the Message Store (see ADR-0001); it needs a
DATABASE_URL but no Telegram user session and no GitHub credentials.
"""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from models import ChannelConfig

# Project root is the directory containing this file
PROJECT_ROOT: Path = Path(__file__).resolve().parent
PROMPT_PATH: Path = PROJECT_ROOT / "prompts" / "digest.md"
CHANNELS_PATH: Path = PROJECT_ROOT / "channels.toml"

# Claude token limit before truncating the message payload
MAX_CONTEXT_TOKENS: int = 120_000

# Required environment variables — the pipeline cannot run without these.
REQUIRED_ENV: tuple[str, ...] = (
    "DATABASE_URL",
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_DIGEST_CHAT_ID",
)


@dataclass(frozen=True)
class Config:
    """Immutable application configuration."""

    database_url: str
    anthropic_api_key: str
    telegram_bot_token: str
    telegram_digest_chat_id: str
    channels: tuple[ChannelConfig, ...]
    prompt_path: Path
    min_message_length: int


def _load_channels(path: Path) -> tuple[tuple[ChannelConfig, ...], dict]:
    """Parse channels.toml and return (allow-list, settings).

    Each [[channels]] entry needs only a chat_id; the title lives in the
    Message Store. Any 'title' in the file is treated as a human-facing comment
    and ignored.
    """
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
        if chat_id is None:
            raise ValueError(f"Channel entry {i} in {path} missing 'chat_id'")
        channels.append(ChannelConfig(chat_id=int(chat_id)))

    return tuple(channels), settings


def _require_env(name: str) -> str:
    """Return an environment variable's value or raise with a clear message."""
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def load_config() -> Config:
    """Load and validate all configuration. Fail fast on missing values."""
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    channels, settings = _load_channels(CHANNELS_PATH)

    return Config(
        database_url=_require_env("DATABASE_URL"),
        anthropic_api_key=_require_env("ANTHROPIC_API_KEY"),
        telegram_bot_token=_require_env("TELEGRAM_BOT_TOKEN"),
        telegram_digest_chat_id=_require_env("TELEGRAM_DIGEST_CHAT_ID"),
        channels=channels,
        prompt_path=PROMPT_PATH,
        min_message_length=settings.get("min_message_length", 30),
    )
