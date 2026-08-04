"""Tomo Home ($TOMO_HOME) bootstrap and path helpers (Alpha Slice 0).

Verifies :func:`ensure_tomo_home` creates the locked §2.1 tree, seeds
``SOUL.md`` / ``tomo.yaml`` from ``defaults/``, auto-creates ``.secret_key``
(chmod 600) only when ``TOMO_SECRET_KEY`` is unset, never creates
``secrets.env``, and is idempotent. ``.env`` bootstrap is covered by
``test_bootstrap`` (install / process start). Also covers the path helpers.
"""

from __future__ import annotations

from pathlib import Path

from app.core import home


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
    assert (root / "library" / "memory").is_dir()
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
    assert home.agent_knowledge_dir("main", root).name == "knowledge"
    # Work dirs live under $TOMO_WORK/<agent>, not $TOMO_HOME/agents/.../work
    work = tmp_path / "workroot"
    assert home.agent_work_dir("main", work) == work / "main"
    assert home.agent_work_dir("ops", work).name == "ops"
    assert home.agent_dir("main", root).parent.name == "agents"
    assert home.library_skills_dir(root).name == "skills"
    assert home.library_memory_dir(root).name == "memory"
    assert home.state_dir(root).name == "state"
    assert home.workplaces_dir(root).name == "workplaces"


def test_default_home_root_is_config(tmp_path: Path) -> None:
    # explicit None root resolves to config.TOMO_HOME (the test temp home)
    from app.core import config

    assert home.soul_path().parent == config.TOMO_HOME
    assert home.state_dir() == config.TOMO_HOME / "state"
