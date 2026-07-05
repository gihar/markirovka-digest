"""Behavior: config comes from env vars + a chat_id allow-list in channels.toml."""

import pytest

import config as config_module
from config import _load_channels, load_config


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


_ALL_ENV = {
    "DATABASE_URL": "postgresql://u:p@host:5432/db",
    "ANTHROPIC_API_KEY": "sk-test",
    "TELEGRAM_BOT_TOKEN": "123:abc",
    "TELEGRAM_DIGEST_CHAT_ID": "-1009999",
}


def _point_config_at(tmp_path, monkeypatch):
    path = _write_channels(tmp_path, "[[channels]]\nchat_id = -1001\n")
    monkeypatch.setattr(config_module, "CHANNELS_PATH", path)


def test_load_config_fails_fast_when_database_url_missing(tmp_path, monkeypatch):
    _point_config_at(tmp_path, monkeypatch)
    for k, v in _ALL_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        load_config()


def test_load_config_reads_database_url_and_allow_list(tmp_path, monkeypatch):
    _point_config_at(tmp_path, monkeypatch)
    for k, v in _ALL_ENV.items():
        monkeypatch.setenv(k, v)
    cfg = load_config()
    assert cfg.database_url == "postgresql://u:p@host:5432/db"
    assert [c.chat_id for c in cfg.channels] == [-1001]
    assert cfg.min_message_length == 30  # default when settings omit it
    assert not hasattr(cfg, "telegram_session")
    assert not hasattr(cfg, "github_token")
