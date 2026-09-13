from __future__ import annotations

from pathlib import Path

import pytest

from app.core import self_update


@pytest.fixture(autouse=True)
def _reset_spawned() -> None:
    self_update._spawned_at = None
    yield
    self_update._spawned_at = None


def test_in_container_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOMO_IN_CONTAINER", "1")
    monkeypatch.delenv("container", raising=False)
    assert self_update.in_container() is True


def test_in_container_dockerenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOMO_IN_CONTAINER", raising=False)
    monkeypatch.delenv("container", raising=False)

    def exists(self: Path) -> bool:
        return str(self) in {"/.dockerenv", "/run/.containerenv"} and str(self) == "/.dockerenv"

    monkeypatch.setattr(Path, "exists", exists)
    assert self_update.in_container() is True


def test_can_self_update_script_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(self_update, "in_container", lambda: False)
    monkeypatch.setattr(self_update, "is_managed_git_install", lambda **k: True)
    assert self_update.can_self_update() == (True, "ok")
    assert self_update.install_kind() == "script"


def test_can_self_update_hidden_in_container(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(self_update, "in_container", lambda: True)
    monkeypatch.setattr(self_update, "is_managed_git_install", lambda **k: True)
    assert self_update.can_self_update() == (False, "container")
    assert self_update.install_kind() == "container"


def test_can_self_update_hidden_for_dev_clone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(self_update, "in_container", lambda: False)
    monkeypatch.setattr(self_update, "is_managed_git_install", lambda **k: False)
    assert self_update.can_self_update() == (False, "not_script_install")
    assert self_update.install_kind() == "dev"


def test_start_update_refuses_when_ineligible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(self_update, "can_self_update", lambda **k: (False, "container"))
    with pytest.raises(PermissionError, match="container"):
        self_update.start_update()
    assert self_update._spawned_at is None


def test_start_update_spawns_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(self_update, "can_self_update", lambda **k: (True, "ok"))
    monkeypatch.setattr(self_update, "_spawn_via_systemd", lambda *a, **k: True)
    self_update.start_update(home=tmp_path)
    assert self_update._spawned_at is not None
    with pytest.raises(RuntimeError, match="already started"):
        self_update.start_update(home=tmp_path)


def test_start_update_retries_after_lockout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(self_update, "can_self_update", lambda **k: (True, "ok"))
    monkeypatch.setattr(self_update, "_spawn_via_systemd", lambda *a, **k: True)
    self_update._spawned_at = (
        self_update.time.monotonic() - self_update._SPAWN_LOCKOUT_SECONDS - 1
    )
    self_update.start_update(home=tmp_path)
    assert self_update._update_in_flight() is True
