# Anti-pattern: Mutable State

## Rule
No global mutable state. No mutable dataclasses. No in-place mutation.

## Bad: mutable dataclass with global instance
```python
@dataclass
class AppState:
    messages: list = field(default_factory=list)

state = AppState()  # global mutable singleton

def add_message(msg):
    state.messages.append(msg)  # mutates global state
```

## Good: frozen dataclass, state passed as argument
```python
@dataclass(frozen=True)
class TelegramMessage:
    message_id: int
    text: str

def save_messages(conn, messages: list[TelegramMessage]) -> int:
    # conn is passed in, not accessed from a global
    # messages are frozen, cannot be modified
    ...
```

## Why
- Frozen dataclasses prevent accidental mutation.
- Passing dependencies as arguments makes testing trivial.
- No hidden side effects from shared global state.
