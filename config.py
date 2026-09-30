"""Configuration loading and validation. Single source of truth.

Loads secrets from environment variables and the chat allow-list from
channels.toml. Fails fast with clear messages on missing or invalid config.

The Digest Service reads from the Message Store (see ADR-0001); it needs a
DATABASE_URL but no Telegram user session and no GitHub credentials.
"""

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from models import ChannelConfig, LlmProvider

# Project root is the directory containing this file
PROJECT_ROOT: Path = Path(__file__).resolve().parent
PROMPT_PATH: Path = PROJECT_ROOT / "prompts" / "digest.md"
CHANNELS_PATH: Path = PROJECT_ROOT / "channels.toml"

# Required environment variables — the pipeline cannot run without these.
REQUIRED_ENV: tuple[str, ...] = (
    "DATABASE_URL",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_DIGEST_CHAT_ID",
)


# Optional secondary LLM provider (ADR-0002): a failed request falls through to
# it instead of costing the day's Digest. Optional as a group — see
# _fallback_provider.
#
# Keep LLM_FALLBACK_MODEL a NON-REASONING model. Reasoning tokens are billed
# against the same max_tokens budget as the answer: on the heaviest real day the
# reasoning variant took 55.7 s and 6542 of 8192 tokens against 11.7 s and 1464
# for the non-reasoning one, so it trips both _HTTP_TIMEOUT and the truncation
# guard in analyzer.py. Raise both caps there before pinning a reasoning model.
FALLBACK_ENV: tuple[str, ...] = (
    "LLM_FALLBACK_BASE_URL",
    "LLM_FALLBACK_API_KEY",
    "LLM_FALLBACK_MODEL",
)


@dataclass(frozen=True)
class Config:
    """Immutable application configuration."""

    database_url: str
    llm_providers: tuple[LlmProvider, ...]  # the primary first
    telegram_bot_token: str
    telegram_digest_chat_id: str
    # Chat that receives failure alerts; None when unset — see _alert_chat_id.
    telegram_alert_chat_id: str | None
    channels: tuple[ChannelConfig, ...]
    prompt_path: Path
    min_message_length: int
    digest_channel_id: int

    @property
    def primary_model(self) -> str:
        """Model of the primary provider — what a normal day's Digest comes from."""
        return self.llm_providers[0].model


# A Telegram hashtag: "#", a letter, then letters, digits or underscores.
# Telegram does not make "#1" or "#мол око" clickable as one tag.
_HASHTAG = re.compile(r"#[^\W\d_]\w*")


def _hashtag(raw: object, i: int, path: Path) -> str | None:
    """Validate a channel's optional industry hashtag."""
    if raw is None:
        return None
    if not isinstance(raw, str) or not _HASHTAG.fullmatch(raw):
        raise ValueError(
            f"Channel entry {i} in {path}: 'hashtag' must look like \"#молоко\", "
            f"got {raw!r}"
        )
    return raw


def _load_channels(path: Path) -> tuple[tuple[ChannelConfig, ...], dict]:
    """Parse channels.toml and return (allow-list, settings).

    Each [[channels]] entry needs a chat_id and may carry an industry hashtag;
    the title lives in the Message Store. Any 'title' in the file is treated as a human-facing comment
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
        channels.append(
            ChannelConfig(
                chat_id=int(chat_id),
                hashtag=_hashtag(ch.get("hashtag"), i, path),
            )
        )

    return tuple(channels), settings


def _digest_channel_id(settings: dict, path: Path) -> int:
    """Return the Digest Channel id from [settings] or raise with a clear message.

    Content originating in the Digest Channel is never input to a Digest, so the
    read path cannot run without this id.
    """
    raw = settings.get("digest_channel_id")
    if raw is None:
        raise ValueError(
            f"Missing 'digest_channel_id' under [settings] in {path}"
        )
    # bool is a subclass of int, and a quoted number is not an id either: the
    # value is matched against a BIGINT column, so require a real TOML integer.
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ValueError(
            f"'digest_channel_id' in {path} must be an integer chat id, "
            f"got {raw!r}"
        )
    return raw


def _require_env(name: str) -> str:
    """Return an environment variable's value or raise with a clear message."""
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _alert_chat_id(digest_chat_id: str) -> str | None:
    """The optional alert chat, or None when it is not configured.

    Optional by design: a run that fails is worth shouting about, but an
    unconfigured alert must never turn a working deployment into a failing one.
    An empty value counts as unset — deployment platforms and .env files hand
    out empty strings for variables nobody filled in.

    The Digest Channel itself is rejected: alerts there would show readers
    internal plumbing, and Telegram forwards channel posts into the linked
    Monitored Chat, feeding every alert back into the next Digest's input. The
    comparison is exact, so it catches the copy-paste mistake but not an
    @username that happens to alias the same chat.
    """
    value = os.environ.get("TELEGRAM_ALERT_CHAT_ID") or None
    if value is not None and value == digest_chat_id:
        raise ValueError(
            f"TELEGRAM_ALERT_CHAT_ID must not be the Digest Channel "
            f"({digest_chat_id}) — alerts belong in a separate chat"
        )
    return value


def _primary_provider() -> LlmProvider:
    """Build the primary LLM provider from its environment variables."""
    return LlmProvider(
        base_url=_require_env("LLM_BASE_URL"),
        api_key=_require_env("LLM_API_KEY"),
        model=_require_env("LLM_MODEL"),
    )


def _fallback_provider() -> LlmProvider | None:
    """Build the optional secondary provider, or None when it is not configured.

    All three variables or none: a half-configured fallback looks configured and
    isn't, so the gap would only surface on the day the primary is down.
    """
    missing = [name for name in FALLBACK_ENV if not os.environ.get(name)]
    if len(missing) == len(FALLBACK_ENV):
        return None
    if missing:
        raise ValueError(
            "Fallback LLM provider is half-configured: set all of "
            f"{', '.join(FALLBACK_ENV)} or none of them. "
            f"Missing: {', '.join(missing)}"
        )
    return LlmProvider(
        base_url=_require_env("LLM_FALLBACK_BASE_URL"),
        api_key=_require_env("LLM_FALLBACK_API_KEY"),
        model=_require_env("LLM_FALLBACK_MODEL"),
    )


def _llm_providers() -> tuple[LlmProvider, ...]:
    """The providers to try, in order: the primary first, the fallback after."""
    primary = _primary_provider()
    fallback = _fallback_provider()
    if fallback is None:
        return (primary,)
    return (primary, fallback)


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
        llm_providers=_llm_providers(),
        telegram_bot_token=_require_env("TELEGRAM_BOT_TOKEN"),
        telegram_digest_chat_id=_require_env("TELEGRAM_DIGEST_CHAT_ID"),
        telegram_alert_chat_id=_alert_chat_id(
            _require_env("TELEGRAM_DIGEST_CHAT_ID")
        ),
        channels=channels,
        prompt_path=PROMPT_PATH,
        min_message_length=settings.get("min_message_length", 30),
        digest_channel_id=_digest_channel_id(settings, CHANNELS_PATH),
    )
