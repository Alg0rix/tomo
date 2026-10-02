"""Consolidated tests (merged from: test_bootstrap.py, test_home.py, test_secrets.py).
- test_bootstrap.py: Bootstrap secrets for install / first start.
- test_home.py: Tomo Home ($TOMO_HOME) bootstrap and path helpers (Alpha Slice 0).
- test_secrets.py: At-rest secret encryption + SQLite settings ciphertext (Alpha Slice 0).
"""

from __future__ import annotations

import os
from pathlib import Path
from app.core.bootstrap import apply_bootstrap_to_config, ensure_bootstrap_secrets
from app.core import config
from app.core import home
import json
import sqlite3
from app.core.secrets import decrypt_secret, encrypt_secret
from app.models.db import get_connection
from app.models.mixins import settings as settings_store
from app.models.schema import migrate


# --- from test_bootstrap.py ---


def test_ensure_bootstrap_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "home"
    monkeypatch.delenv("TOMO_SESSION_SECRET", raising=False)
    monkeypatch.delenv("TOMO_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("TOMO_SECRET_KEY", raising=False)
    for k in ("TOMO_SESSION_SECRET", "TOMO_ADMIN_PASSWORD"):
        os.environ.pop(k, None)

    first = ensure_bootstrap_secrets(root)
    env_text = first.env_path.read_text(encoding="utf-8")
    sk = first.secret_key_path.read_bytes()
    admin = first.admin_password

    second = ensure_bootstrap_secrets(root)
    assert not second.created_session_secret
    assert not second.created_admin_password
    assert not second.created_secret_key
    assert second.admin_password == ""
    assert first.env_path.read_text(encoding="utf-8") == env_text
    assert first.secret_key_path.read_bytes() == sk
    assert os.environ["TOMO_ADMIN_PASSWORD"] == admin




def test_apply_bootstrap_to_config(monkeypatch) -> None:
    monkeypatch.setenv("TOMO_SESSION_SECRET", "cfg-session")
    monkeypatch.setenv("TOMO_ADMIN_PASSWORD", "cfg-admin")
    apply_bootstrap_to_config()
    assert config.SESSION_SECRET == "cfg-session"
    assert config.ADMIN_PASSWORD == "cfg-admin"


# --- from test_home.py ---




def test_secret_key_skipped_when_env_master_key_set(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TOMO_SECRET_KEY", "env-master-key-present")
    root = tmp_path / "home"
    home.ensure_tomo_home(root)
    # env key wins -> no .secret_key file is created
    assert not (root / ".secret_key").exists()




def test_agent_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TOMO_SECRET_KEY", raising=False)
    root = tmp_path / "h"
    home.ensure_tomo_home(root)
    assert home.agent_system_path("main", root).name == "SYSTEM.md"
    assert home.agent_soul_path("main", root).name == "SOUL.md"
    # Work dirs live under $TOMO_WORK/<agent>, not $TOMO_HOME/agents/.../work
    work = tmp_path / "workroot"
    assert home.agent_work_dir("main", work) == work / "main"
    assert home.agent_work_dir("ops", work).name == "ops"
    assert home.agent_dir("main", root).parent.name == "agents"
    assert home.library_skills_dir(root).name == "skills"
    assert home.state_dir(root).name == "state"
    assert home.workplaces_dir(root).name == "workplaces"




# --- from test_secrets.py ---
def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = get_connection(tmp_path / "secrets.db")
    migrate(conn)
    return conn


# --- core crypto ---------------------------------------------------------






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


