"""Tomo Home ($TOMO_HOME) — path helpers and bootstrap.

Owns the **config** root (default ``~/.tomo``), not agent workspaces::

    $TOMO_HOME/                 # config / state only
    ├── tomo.yaml
    ├── .env / .secret_key
    ├── SOUL.md
    ├── library/{skills,memory}
    ├── agents/<id>/{SYSTEM.md,SOUL.md,knowledge}
    ├── workplaces/
    └── state/tomo.db

Agent tool cwd (when no local workplace is bound) lives under
``$TOMO_WORK`` (default ``~/tomo``)::

    $TOMO_WORK/<agent_id>/      # e.g. ~/tomo/ops

:func:`ensure_tomo_home` creates the tree on first run and seeds ``SOUL.md`` /
``tomo.yaml`` from the shipped ``defaults/`` (copy, never bind-mount the repo as
live config). The master ``.secret_key`` is auto-created (chmod 600) only when
``TOMO_SECRET_KEY`` is unset and no key file exists — it is never overwritten.
No API keys or secrets are written into home files; the optional ``.env`` is
never auto-created. Allowed familiar names: ``SOUL.md``, ``SYSTEM.md``,
``.env``, ``.secret_key``; no ``secrets.env`` / ``identity.md`` / ``prompt.md``.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet

from app.core import config

_DEFAULTS_DIR = config.REPO_ROOT / "defaults"
_SECRET_KEY_FILE = ".secret_key"


def _root(root: Path | None) -> Path:
    """Resolve the home root: explicit arg wins, else :data:`config.TOMO_HOME`."""
    return Path(root) if root is not None else config.TOMO_HOME


def soul_path(root: Path | None = None) -> Path:
    return _root(root) / "SOUL.md"


def tomo_yaml_path(root: Path | None = None) -> Path:
    return _root(root) / "tomo.yaml"


def secret_key_path(root: Path | None = None) -> Path:
    return _root(root) / _SECRET_KEY_FILE


def agent_dir(agent_id: str, root: Path | None = None) -> Path:
    return _root(root) / "agents" / agent_id


def agent_system_path(agent_id: str, root: Path | None = None) -> Path:
    return agent_dir(agent_id, root) / "SYSTEM.md"


def agent_soul_path(agent_id: str, root: Path | None = None) -> Path:
    return agent_dir(agent_id, root) / "SOUL.md"


def agent_knowledge_dir(agent_id: str, root: Path | None = None) -> Path:
    return agent_dir(agent_id, root) / "knowledge"


def work_root(root: Path | None = None) -> Path:
    """Agent workspace root (``$TOMO_WORK``, default ``~/tomo``).

    Separate from ``$TOMO_HOME`` config. ``root`` overrides for tests.
    """
    if root is not None:
        return Path(root)
    return config.TOMO_WORK


def agent_work_dir(agent_id: str, root: Path | None = None) -> Path:
    """Per-agent tool cwd: ``$TOMO_WORK/<agent_id>`` (not under ``~/.tomo``).

    Note: ``root`` here is an optional **work** root override (tests), not
    ``$TOMO_HOME``. Config paths still use ``agent_dir(..., root=TOMO_HOME)``.
    """
    aid = (agent_id or "").strip() or "_default"
    if "/" in aid or "\\" in aid or ".." in aid or aid in {".", ".."}:
        aid = "_default"
    return work_root(root) / aid


def library_skills_dir(root: Path | None = None) -> Path:
    return _root(root) / "library" / "skills"


def library_memory_dir(root: Path | None = None) -> Path:
    return _root(root) / "library" / "memory"


def workplaces_dir(root: Path | None = None) -> Path:
    return _root(root) / "workplaces"


def state_dir(root: Path | None = None) -> Path:
    return _root(root) / "state"


def _seed_file(target: Path, source: Path) -> None:
    """Copy ``source`` -> ``target`` only when ``target`` is missing."""
    if target.exists():
        return
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _ensure_secret_key(root: Path) -> None:
    """Create ``.secret_key`` (chmod 600) when no env master key and file missing.

    Skipped when ``TOMO_SECRET_KEY`` is set (env is the preferred source) or when
    the file already exists (never overwrite). Uses a fresh Fernet key.
    """
    if (os.environ.get("TOMO_SECRET_KEY") or "").strip():
        return
    sk = root / _SECRET_KEY_FILE
    if sk.exists():
        return
    sk.write_bytes(Fernet.generate_key())
    try:
        os.chmod(sk, 0o600)
    except OSError:
        pass


def ensure_tomo_home(root: Path | None = None) -> Path:
    """Create the ``$TOMO_HOME`` tree and seed defaults; idempotent.

    Creates the layout directories, seeds ``SOUL.md`` / ``tomo.yaml`` from
    ``defaults/`` when missing, and auto-creates ``.secret_key`` (chmod 600)
    only when ``TOMO_SECRET_KEY`` is unset and the file is absent. Never writes
    ``.env`` or any API key, and never overwrites an existing ``.secret_key``.
    Returns the resolved root :class:`Path`.
    """
    home_root = _root(root)
    home_root.mkdir(parents=True, exist_ok=True)

    for d in (
        library_skills_dir(home_root),
        library_memory_dir(home_root),
        home_root / "agents",
        workplaces_dir(home_root),
        state_dir(home_root),
    ):
        d.mkdir(parents=True, exist_ok=True)

    # Work root is separate from config (default ~/tomo).
    try:
        work_root().mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    _seed_file(soul_path(home_root), _DEFAULTS_DIR / "SOUL.md")
    _seed_file(tomo_yaml_path(home_root), _DEFAULTS_DIR / "tomo.yaml")
    _ensure_secret_key(home_root)
    return home_root


__all__ = [
    "ensure_tomo_home",
    "soul_path",
    "tomo_yaml_path",
    "secret_key_path",
    "agent_dir",
    "agent_system_path",
    "agent_soul_path",
    "agent_knowledge_dir",
    "work_root",
    "agent_work_dir",
    "library_skills_dir",
    "library_memory_dir",
    "workplaces_dir",
    "state_dir",
]
