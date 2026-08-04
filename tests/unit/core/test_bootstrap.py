"""Bootstrap secrets for install / first start."""

from __future__ import annotations

import os
from pathlib import Path

from app.core.bootstrap import apply_bootstrap_to_config, ensure_bootstrap_secrets
from app.core import config


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
