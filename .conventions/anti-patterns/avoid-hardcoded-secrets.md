# Anti-pattern: Hardcoded Secrets

## Rule
All secrets must come from environment variables. Never commit tokens,
API keys, or passwords to source code.

## Bad: secret in source code
```python
TELEGRAM_TOKEN = "123456:ABC-DEF"
API_KEY = "sk-proj-abcdef123456"

client = httpx.AsyncClient(headers={"Authorization": f"Bearer {API_KEY}"})
```

## Good: secrets from environment with fail-fast validation
```python
def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value

config = Config(
    api_key=_require_env("ANTHROPIC_API_KEY"),
    bot_token=_require_env("TELEGRAM_BOT_TOKEN"),
)
```

## Why
- Secrets in code get committed to git history permanently.
- Environment variables are the standard for CI/CD and containers.
- Fail-fast validation surfaces missing config at startup, not at runtime.
