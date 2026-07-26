"""At-rest secret encryption + SQLite settings ciphertext (Alpha Slice 0).

Covers :mod:`app.core.secrets` (encrypt/decrypt round-trip, empty handling,
refusal of plaintext) and the settings mixin invariant: ``llm_api_key`` is
ciphertext in the raw DB after a write, decrypted in memory for runtime
``get_settings``, and still masked by ``public_settings``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.core.secrets import decrypt_secret, encrypt_secret
from app.models.db import get_connection
from app.models.mixins import settings as settings_store
from app.models.schema import migrate


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = get_connection(tmp_path / "secrets.db")
    migrate(conn)
    return conn


# --- core crypto ---------------------------------------------------------


def test_encrypt_decrypt_round_trip() -> None:
    ct = encrypt_secret("sk-test-12345")
    assert ct.startswith("enc:v1:")
    assert "sk-test-12345" not in ct
    assert decrypt_secret(ct) == "sk-test-12345"


def test_encrypt_empty_is_empty() -> None:
    assert encrypt_secret("") == ""
    assert encrypt_secret(None) == ""
    assert decrypt_secret("") == ""
    assert decrypt_secret(None) == ""


def test_decrypt_refuses_plaintext() -> None:
    # a non-empty value without the enc:v1: prefix is refused (Alpha invariant)
    assert decrypt_secret("sk-plaintext-leak") == ""


def test_decrypt_bad_token_returns_empty() -> None:
    assert decrypt_secret("enc:v1:not-a-real-fernet-token") == ""


# --- settings mixin invariant -------------------------------------------


def test_settings_stores_ciphertext_not_plaintext(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    settings_store.update_settings(conn, {"llm_api_key": "sk-test-secret"})
    raw = conn.execute(
        "SELECT value_json FROM settings WHERE key='llm_api_key'"
    ).fetchone()["value_json"]
    assert "sk-test-secret" not in raw
    assert json.loads(raw).startswith("enc:v1:")


def test_get_settings_decrypts_for_runtime(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    settings_store.update_settings(conn, {"llm_api_key": "sk-runtime-key"})
    got = settings_store.get_settings(conn)
    assert got["llm_api_key"] == "sk-runtime-key"


def test_public_settings_masks_decrypted(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    settings_store.update_settings(conn, {"llm_api_key": "sk-abcdefghijklmnop"})
    pub = settings_store.public_settings(settings_store.get_settings(conn))
    assert pub["llm_api_key_set"] is True
    assert pub["llm_api_key"] == "••••mnop"
    assert "sk-abcdef" not in pub["llm_api_key"]


def test_blank_put_keeps_existing_ciphertext(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    settings_store.update_settings(conn, {"llm_api_key": "sk-keep-me"})
    settings_store.update_settings(conn, {"llm_api_key": "", "llm_model": "gpt-4o"})
    assert settings_store.get_settings(conn)["llm_api_key"] == "sk-keep-me"
    assert settings_store.get_settings(conn)["llm_model"] == "gpt-4o"


def test_seed_empty_key_stays_empty(tmp_path: Path) -> None:
    # an empty seeded key is not a secret: round-trips as "" (no ciphertext)
    conn = _conn(tmp_path)
    conn.execute(
        "INSERT INTO settings (key, value_json) VALUES (?,?)",
        ("llm_api_key", json.dumps("")),
    )
    conn.commit()
    assert settings_store.get_settings(conn)["llm_api_key"] == ""
