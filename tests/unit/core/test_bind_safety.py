"""Config / bind-safety guards."""

from __future__ import annotations

import pytest

from app.core import config


def test_assert_bind_safety_allows_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "HOST", "127.0.0.1")
    monkeypatch.setattr(config, "SESSION_SECRET", config._DEFAULT_SESSION_SECRET)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", config._DEFAULT_ADMIN_PASSWORD)
    config.assert_bind_safety()  # does not raise


def test_assert_bind_safety_refuses_public_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "HOST", "0.0.0.0")
    monkeypatch.setattr(config, "PORT", 8787)
    monkeypatch.setattr(config, "SESSION_SECRET", config._DEFAULT_SESSION_SECRET)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "strong-enough-password")
    with pytest.raises(SystemExit, match="TOMO_SESSION_SECRET"):
        config.assert_bind_safety()
