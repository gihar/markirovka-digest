"""Gold standard: configuration loading with fail-fast validation.

Rules:
- All secrets come from environment variables (never hardcoded).
- Validate all required env vars upfront; raise with a clear message.
- Use a frozen dataclass for the Config object (immutable after load).
- Use UPPER_CASE module-level constants for paths and the required-var list.
Extracted from config.py.
"""

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parent
PROMPT_PATH: Path = PROJECT_ROOT / "prompts" / "digest.md"

REQUIRED_ENV: tuple[str, ...] = (
    "DATABASE_URL",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
)


def _require_env(name: str) -> str:
    """Return an env var's value or raise with a clear message."""
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Config:
    """Immutable application configuration."""

    database_url: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    prompt_path: Path


def load_config() -> Config:
    """Load and validate all configuration. Fail fast on missing values."""
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )
    return Config(
        database_url=_require_env("DATABASE_URL"),
        llm_base_url=_require_env("LLM_BASE_URL"),
        llm_api_key=_require_env("LLM_API_KEY"),
        llm_model=_require_env("LLM_MODEL"),
        prompt_path=PROMPT_PATH,
    )
