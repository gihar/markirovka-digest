"""Gold standard: configuration loading with fail-fast validation.

Rules:
- All secrets come from environment variables (never hardcoded).
- Validate all required env vars upfront; raise with a clear message.
- Use a frozen dataclass for the Config object (immutable after load).
- Use UPPER_CASE module-level constants for paths and limits.
Extracted from config.py.
"""

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parent
DB_PATH: Path = PROJECT_ROOT / "data" / "messages.db"
MAX_CONTEXT_TOKENS: int = 120_000


def _require_env(name: str) -> str:
    """Return an env var's value or raise with a clear message."""
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Config:
    """Immutable application configuration."""

    api_key: str
    db_path: Path


def load_config() -> Config:
    """Load and validate all configuration. Fail fast on missing values."""
    missing = [k for k in ["API_KEY"] if not os.environ.get(k)]
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )
    return Config(api_key=_require_env("API_KEY"), db_path=DB_PATH)
