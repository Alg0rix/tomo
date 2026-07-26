"""Telegram settings: encrypted token, masked GET, blank-PUT keep, enable flag."""

from __future__ import annotations

import json

from app.services import store


def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "tg-settings.db")


def test_seed_includes_telegram_keys(tmp_path) -> None:
    _rebind(tmp_path)
    s = store.get_settings()
    assert s["telegram_bot_token"] == ""
    assert s["telegram_enabled"] is False


def test_public_settings_masks_telegram_token(tmp_path) -> None:
    _rebind(tmp_path)
    store.update_settings({"telegram_bot_token": "123456:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"})
    pub = store.get_public_settings()
    assert pub["telegram_bot_token_set"] is True
    assert pub["telegram_bot_token"] == "••••Dsaw"
    assert "AAHdqTcv" not in pub["telegram_bot_token"]
    assert store.get_settings()["telegram_bot_token"].startswith("123456:")


def test_blank_put_keeps_telegram_token(tmp_path) -> None:
    _rebind(tmp_path)
    store.update_settings({"telegram_bot_token": "keep-token-secret99"})
    store.update_settings({"telegram_bot_token": "", "telegram_enabled": True})
    assert store.get_settings()["telegram_bot_token"] == "keep-token-secret99"
    assert store.get_settings()["telegram_enabled"] is True


def test_telegram_token_ciphertext_at_rest(tmp_path) -> None:
    from app.models.db import get_connection
    from app.models.mixins import settings as settings_store
    from app.models.schema import migrate

    conn = get_connection(tmp_path / "tg-cipher.db")
    migrate(conn)
    settings_store.update_settings(conn, {"telegram_bot_token": "secret-bot-token"})
    raw = conn.execute(
        "SELECT value_json FROM settings WHERE key='telegram_bot_token'"
    ).fetchone()["value_json"]
    assert "secret-bot-token" not in raw
    assert json.loads(raw).startswith("enc:v1:")
    conn.close()


def test_channel_status_needs_token_connected_off(tmp_path) -> None:
    from app.channels.telegram import telegram_status

    _rebind(tmp_path)
    assert telegram_status() == "needs_token"
    store.update_settings({"telegram_bot_token": "tok-abcdefg", "telegram_enabled": False})
    assert telegram_status() == "off"
    store.update_settings({"telegram_enabled": True})
    assert telegram_status() == "connected"


def test_agent_and_shared_channels_reflect_status(tmp_path) -> None:
    _rebind(tmp_path)
    chans = store.get_agent_channels("main")
    tg = next(c for c in chans if c["type"] == "telegram")
    assert tg["status"] == "needs_token"
    shared = store.list_shared_channels()
    sc = next(c for c in shared if c["type"] == "telegram")
    assert sc["status"] == "needs_token"

    store.update_settings({"telegram_bot_token": "tok-xyz", "telegram_enabled": True})
    assert next(c for c in store.get_agent_channels("main") if c["type"] == "telegram")[
        "status"
    ] == "connected"
    assert next(c for c in store.list_shared_channels() if c["type"] == "telegram")[
        "status"
    ] == "connected"
