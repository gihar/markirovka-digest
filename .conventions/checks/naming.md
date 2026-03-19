# Naming Conventions

## Pass/Fail Rules

| Element            | Convention           | Example                  |
|--------------------|----------------------|--------------------------|
| Modules            | snake_case           | `config.py`, `db.py`    |
| Functions          | snake_case           | `load_config()`         |
| Private functions  | _snake_case          | `_require_env()`        |
| Constants          | UPPER_SNAKE_CASE     | `MAX_CONTEXT_TOKENS`    |
| Dataclasses        | PascalCase, frozen   | `TelegramMessage`       |
| Variables          | snake_case           | `chat_id`, `msg_date`   |

## FAIL conditions
- Module file named in camelCase or PascalCase.
- Constant defined in lowercase (e.g., `max_tokens = 100`).
- Dataclass without `frozen=True`.
- Function name in camelCase (e.g., `loadConfig`).

## PASS conditions
- All module files use snake_case.
- All module-level constants use UPPER_SNAKE_CASE.
- All dataclasses use `@dataclass(frozen=True)`.
- All functions and variables use snake_case.
