"""Behavior: config comes from env vars + a chat_id allow-list in channels.toml."""

import pytest

import config as config_module
from config import (
    CHANNELS_PATH,
    _digest_channel_id,
    _load_channels,
    load_config,
)
from models import LlmProvider


def _write_channels(tmp_path, body: str):
    path = tmp_path / "channels.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_allow_list_is_a_plain_list_of_chat_ids(tmp_path):
    path = _write_channels(
        tmp_path,
        "[settings]\nmin_message_length = 10\n\n"
        "[[channels]]\nchat_id = -1001\n\n"
        "[[channels]]\nchat_id = -1002\n",
    )
    channels, settings = _load_channels(path)
    assert [c.chat_id for c in channels] == [-1001, -1002]
    assert settings["min_message_length"] == 10


def test_channel_entry_without_chat_id_is_rejected(tmp_path):
    path = _write_channels(tmp_path, '[[channels]]\ntitle = "no id here"\n')
    with pytest.raises(ValueError, match="chat_id"):
        _load_channels(path)


def test_empty_allow_list_is_rejected(tmp_path):
    path = _write_channels(tmp_path, "[settings]\nmin_message_length = 5\n")
    with pytest.raises(ValueError, match="No \\[\\[channels\\]\\]"):
        _load_channels(path)


def test_a_channel_may_carry_an_industry_hashtag(tmp_path):
    path = _write_channels(
        tmp_path,
        '[[channels]]\nchat_id = -1001\nhashtag = "#молоко"\n\n'
        "[[channels]]\nchat_id = -1002\n",
    )
    channels, _ = _load_channels(path)
    assert [c.hashtag for c in channels] == ["#молоко", None]


@pytest.mark.parametrize("raw", ['"молоко"', '"#"', '"#мол око"', '"#1молоко"', "5"])
def test_a_malformed_hashtag_is_rejected(tmp_path, raw):
    path = _write_channels(tmp_path, f"[[channels]]\nchat_id = -1001\nhashtag = {raw}\n")
    with pytest.raises(ValueError, match="hashtag"):
        _load_channels(path)


def test_shipped_channels_toml_tags_the_industry_chats():
    channels, _ = _load_channels(CHANNELS_PATH)
    tags = {c.chat_id: c.hashtag for c in channels}
    assert tags[-1001359438834] == "#молоко"
    assert tags[-1001329208283] == "#легпром"
    assert tags[-1002202471035] == "#электроника"


_ALL_ENV = {
    "DATABASE_URL": "postgresql://u:p@host:5432/db",
    "LLM_BASE_URL": "https://openrouter.ai/api/v1",
    "LLM_API_KEY": "sk-or-test",
    "LLM_MODEL": "anthropic/claude-sonnet-4.6",
    "TELEGRAM_BOT_TOKEN": "123:abc",
    "TELEGRAM_DIGEST_CHAT_ID": "-1009999",
}


def _point_config_at(tmp_path, monkeypatch):
    path = _write_channels(
        tmp_path,
        "[settings]\ndigest_channel_id = -1001383199989\n\n"
        "[[channels]]\nchat_id = -1001\n",
    )
    monkeypatch.setattr(config_module, "CHANNELS_PATH", path)


def test_load_config_fails_fast_when_database_url_missing(tmp_path, monkeypatch):
    _point_config_at(tmp_path, monkeypatch)
    for k, v in _ALL_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        load_config()


def test_load_config_fails_fast_when_llm_model_missing(tmp_path, monkeypatch):
    _point_config_at(tmp_path, monkeypatch)
    for k, v in _ALL_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(ValueError, match="LLM_MODEL"):
        load_config()


_FALLBACK_ENV = {
    "LLM_FALLBACK_BASE_URL": "https://api.neuraldeep.ru/v1",
    "LLM_FALLBACK_API_KEY": "nd-test",
    "LLM_FALLBACK_MODEL": "qwen3.6-unlim-noreason",
}


def _without_fallback(monkeypatch):
    """No fallback in the environment — the pre-ADR-0002 single-provider setup."""
    for k in _FALLBACK_ENV:
        monkeypatch.delenv(k, raising=False)


_PRIMARY = LlmProvider(
    base_url="https://openrouter.ai/api/v1",
    api_key="sk-or-test",
    model="anthropic/claude-sonnet-4.6",
)


def test_load_config_without_a_fallback_reads_one_provider(tmp_path, monkeypatch):
    _point_config_at(tmp_path, monkeypatch)
    for k, v in _ALL_ENV.items():
        monkeypatch.setenv(k, v)
    _without_fallback(monkeypatch)
    cfg = load_config()
    assert cfg.database_url == "postgresql://u:p@host:5432/db"
    assert cfg.llm_providers == (_PRIMARY,)
    assert [c.chat_id for c in cfg.channels] == [-1001]
    assert cfg.min_message_length == 30  # default when settings omit it
    assert not hasattr(cfg, "anthropic_api_key")
    assert not hasattr(cfg, "telegram_session")


def test_load_config_with_a_fallback_puts_the_primary_first(tmp_path, monkeypatch):
    _point_config_at(tmp_path, monkeypatch)
    for k, v in {**_ALL_ENV, **_FALLBACK_ENV}.items():
        monkeypatch.setenv(k, v)
    cfg = load_config()
    assert cfg.llm_providers == (
        _PRIMARY,
        LlmProvider(
            base_url="https://api.neuraldeep.ru/v1",
            api_key="nd-test",
            model="qwen3.6-unlim-noreason",
        ),
    )


def test_half_configured_fallback_is_rejected(tmp_path, monkeypatch):
    """A fallback that looks configured and isn't is worse than none at all."""
    _point_config_at(tmp_path, monkeypatch)
    for k, v in {**_ALL_ENV, **_FALLBACK_ENV}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("LLM_FALLBACK_API_KEY")

    with pytest.raises(ValueError, match="LLM_FALLBACK_API_KEY"):
        load_config()


def test_load_config_reads_the_digest_channel_id(tmp_path, monkeypatch):
    # The Digest Channel id is numeric config, deliberately separate from
    # TELEGRAM_DIGEST_CHAT_ID: that one is an addressing string and may be an
    # @username, which cannot be matched against the id the Scraper stores.
    path = _write_channels(
        tmp_path,
        "[settings]\ndigest_channel_id = -1001383199989\n\n"
        "[[channels]]\nchat_id = -1001\n",
    )
    monkeypatch.setattr(config_module, "CHANNELS_PATH", path)
    for k, v in _ALL_ENV.items():
        monkeypatch.setenv(k, v)

    cfg = load_config()

    assert cfg.digest_channel_id == -1001383199989


def test_missing_digest_channel_id_is_rejected(tmp_path, monkeypatch):
    path = _write_channels(tmp_path, "[[channels]]\nchat_id = -1001\n")
    monkeypatch.setattr(config_module, "CHANNELS_PATH", path)
    for k, v in _ALL_ENV.items():
        monkeypatch.setenv(k, v)

    with pytest.raises(ValueError, match="digest_channel_id"):
        load_config()


@pytest.mark.parametrize(
    "raw",
    ['"-1001383199989"', '"канал дайджеста"', "true", "-1001383199989.5"],
    ids=["quoted-number", "text", "boolean", "float"],
)
def test_non_integer_digest_channel_id_is_rejected(tmp_path, monkeypatch, raw):
    # The id is matched against a BIGINT column in the Message Store, so only a
    # TOML integer will do — a quoted number is config drift waiting to happen.
    path = _write_channels(
        tmp_path,
        f"[settings]\ndigest_channel_id = {raw}\n\n"
        "[[channels]]\nchat_id = -1001\n",
    )
    monkeypatch.setattr(config_module, "CHANNELS_PATH", path)
    for k, v in _ALL_ENV.items():
        monkeypatch.setenv(k, v)

    with pytest.raises(ValueError, match="digest_channel_id"):
        load_config()


def test_shipped_channels_toml_declares_the_digest_channel():
    # Guards the deployed file itself: without the id in [settings] every run
    # would fail at config load, and the exclusion it drives would be silently
    # absent from the query.
    _, settings = _load_channels(CHANNELS_PATH)

    assert isinstance(_digest_channel_id(settings, CHANNELS_PATH), int)


def test_alert_chat_is_optional_and_absent_by_default(tmp_path, monkeypatch):
    # An unconfigured alert must never turn a working deployment into a failing
    # one: no TELEGRAM_ALERT_CHAT_ID simply means no alerts.
    _point_config_at(tmp_path, monkeypatch)
    for k, v in _ALL_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TELEGRAM_ALERT_CHAT_ID", raising=False)

    cfg = load_config()

    assert cfg.telegram_alert_chat_id is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("-1005555555", "-1005555555"), ("@digest_alerts", "@digest_alerts"), ("", None)],
    ids=["numeric-id", "username", "empty-counts-as-unset"],
)
def test_alert_chat_is_read_from_the_environment(tmp_path, monkeypatch, raw, expected):
    _point_config_at(tmp_path, monkeypatch)
    for k, v in _ALL_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_ID", raw)

    cfg = load_config()

    assert cfg.telegram_alert_chat_id == expected


def test_alert_chat_must_not_be_the_digest_chat(tmp_path, monkeypatch):
    # Alerts in the Digest Channel would show readers internal plumbing, and
    # Telegram forwards channel posts into the linked Monitored Chat — feeding
    # the alert straight back into the next Digest's input.
    _point_config_at(tmp_path, monkeypatch)
    for k, v in _ALL_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_ID", _ALL_ENV["TELEGRAM_DIGEST_CHAT_ID"])

    with pytest.raises(ValueError, match="TELEGRAM_ALERT_CHAT_ID"):
        load_config()
