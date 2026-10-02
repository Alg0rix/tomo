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
def test_ensure_bootstrap_creates_env_and_secret_key(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "home"
    monkeypatch.delenv("TOMO_SESSION_SECRET", raising=False)
    monkeypatch.delenv("TOMO_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("TOMO_SECRET_KEY", raising=False)
    # Clear any values loaded from the test home .env at import time.
    for k in ("TOMO_SESSION_SECRET", "TOMO_ADMIN_PASSWORD"):
        os.environ.pop(k, None)

    result = ensure_bootstrap_secrets(root)

    assert result.created_session_secret
    assert result.created_admin_password
    assert result.created_secret_key
    assert result.admin_password
    assert result.env_path.is_file()
    assert result.env_path.stat().st_mode & 0o777 == 0o600
    text = result.env_path.read_text(encoding="utf-8")
    assert "TOMO_SESSION_SECRET=" in text
    assert "TOMO_ADMIN_PASSWORD=" in text
    assert result.secret_key_path.is_file()
    assert result.secret_key_path.stat().st_mode & 0o777 == 0o600
    assert os.environ["TOMO_SESSION_SECRET"]
    assert os.environ["TOMO_ADMIN_PASSWORD"] == result.admin_password


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


def test_env_process_wins_over_file(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "home"
    monkeypatch.setenv("TOMO_SESSION_SECRET", "from-process")
    monkeypatch.setenv("TOMO_ADMIN_PASSWORD", "from-process-admin")
    monkeypatch.delenv("TOMO_SECRET_KEY", raising=False)

    result = ensure_bootstrap_secrets(root)
    assert not result.created_session_secret
    assert not result.created_admin_password
    assert result.created_secret_key
    assert not result.env_path.exists() or "TOMO_SESSION_SECRET=" not in result.env_path.read_text(
        encoding="utf-8"
    )


def test_apply_bootstrap_to_config(monkeypatch) -> None:
    monkeypatch.setenv("TOMO_SESSION_SECRET", "cfg-session")
    monkeypatch.setenv("TOMO_ADMIN_PASSWORD", "cfg-admin")
    apply_bootstrap_to_config()
    assert config.SESSION_SECRET == "cfg-session"
    assert config.ADMIN_PASSWORD == "cfg-admin"


# --- from test_home.py ---
def test_ensure_tomo_home_creates_tree(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "tomo-home"
    monkeypatch.setenv("TOMO_HOME", str(root))
    monkeypatch.delenv("TOMO_SECRET_KEY", raising=False)

    got = home.ensure_tomo_home(root)
    assert got == root

    # locked §2.1 layout
    assert (root / "SOUL.md").is_file()
    assert (root / "tomo.yaml").is_file()
    assert (root / "library" / "skills").is_dir()
    assert (root / "memory" / "vault").is_dir()
    assert (root / "agents").is_dir()
    assert (root / "workplaces").is_dir()
    assert (root / "state").is_dir()

    # forbidden / never-auto-created files
    assert not (root / "secrets.env").exists()
    assert not (root / ".env").exists()

    # master key auto-created, chmod 600, non-trivial length
    sk = root / ".secret_key"
    assert sk.is_file()
    assert sk.stat().st_mode & 0o777 == 0o600
    assert len(sk.read_text(encoding="utf-8").strip()) >= 32


def test_ensure_tomo_home_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TOMO_SECRET_KEY", raising=False)
    root = tmp_path / "home"
    home.ensure_tomo_home(root)
    soul = (root / "SOUL.md").read_text(encoding="utf-8")
    yaml = (root / "tomo.yaml").read_text(encoding="utf-8")
    sk = (root / ".secret_key").read_bytes()

    # second call is a no-op for existing files (never overwrites)
    home.ensure_tomo_home(root)
    assert (root / "SOUL.md").read_text(encoding="utf-8") == soul
    assert (root / "tomo.yaml").read_text(encoding="utf-8") == yaml
    assert (root / ".secret_key").read_bytes() == sk


def test_secret_key_skipped_when_env_master_key_set(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TOMO_SECRET_KEY", "env-master-key-present")
    root = tmp_path / "home"
    home.ensure_tomo_home(root)
    # env key wins -> no .secret_key file is created
    assert not (root / ".secret_key").exists()


def test_soul_seeded_from_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TOMO_SECRET_KEY", raising=False)
    root = tmp_path / "home"
    home.ensure_tomo_home(root)
    text = (root / "SOUL.md").read_text(encoding="utf-8").strip()
    assert len(text) > 0
    assert (root / "tomo.yaml").read_text(encoding="utf-8").strip()


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


def test_default_home_root_is_config(tmp_path: Path) -> None:
    # explicit None root resolves to config.TOMO_HOME (the test temp home)
    from app.core import config

    assert home.soul_path().parent == config.TOMO_HOME
    assert home.state_dir() == config.TOMO_HOME / "state"


# --- from test_secrets.py ---
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


