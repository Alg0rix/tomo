"""Application configuration.

Reads from environment variables with sensible local defaults. The single
admin password is intentionally simple for local development — replace via
the TOMO_ADMIN_PASSWORD env var (or a secrets manager) in any real deploy.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = APP_DIR / "static"
TEMPLATE_DIR = APP_DIR / "templates"
DATA_DIR = APP_DIR / "data"

REPO_ROOT = APP_DIR.parent


def _load_home_env(home_root: Path) -> None:
    """Load ``$TOMO_HOME/.env`` into ``os.environ`` (override=False; process env wins).

    Minimal KEY=VAL parser (no external dependency). Skips blank/comment lines
    and strips surrounding quotes. ``TOMO_HOME`` itself cannot be set here (it
    locates the file). Never logs values. No-op when the file is absent.
    """
    env_file = home_root / ".env"
    if not env_file.is_file():
        return
    try:
        text = env_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


# --- Tomo Home ($TOMO_HOME) ---
# Config / state only (default ~/.tomo): SOUL, secrets, DB, library.
# Agent *work* directories live separately under $TOMO_WORK (default ~/tomo).
TOMO_HOME = Path(
    os.environ.get("TOMO_HOME", str(Path.home() / ".tomo"))
).expanduser()
# Optional bootstrap .env is loaded before the config lines below so it can
# supply e.g. TOMO_DB_PATH / TOMO_ADMIN_PASSWORD (process env still wins).
_load_home_env(TOMO_HOME)
# Sandbox / tool cwd when no local workplace is bound (default ~/tomo/<agent_id>).
TOMO_WORK = Path(
    os.environ.get("TOMO_WORK", str(Path.home() / "tomo"))
).expanduser()
VAR_DIR = Path(os.environ.get("TOMO_VAR_DIR", str(TOMO_HOME / "state")))
DB_PATH = Path(os.environ.get("TOMO_DB_PATH", str(VAR_DIR / "tomo.db")))


# --- Server ---
HOST = os.environ.get("TOMO_HOST", "127.0.0.1")
PORT = int(os.environ.get("TOMO_PORT", "8787"))
RELOAD = _env_bool("TOMO_RELOAD", default=False)

# --- Auth ---
# Session-cookie signing secret (NOT the at-rest master key). The master key for
# encrypting SQLite secrets is TOMO_SECRET_KEY (see app.core.secrets). Set
# TOMO_SESSION_SECRET to a stable value in any real deploy; the dev default is
# single-user only.
SESSION_SECRET = os.environ.get("TOMO_SESSION_SECRET", "tomo-dev-secret-change-me")
# Seed password for the bootstrap ``admin`` account when the users table is empty.
# After bootstrap, change passwords via System → Accounts (env is seed-only).
ADMIN_PASSWORD = os.environ.get("TOMO_ADMIN_PASSWORD", "tomo")
SESSION_COOKIE_NAME = "tomo_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

# --- Brand ---
BRAND = "Tomo"
BRAND_SUBTITLE = "友達 · agent swarm"

# --- Feature flags ---
# Eval / evaluator UI + API are deferred (roadmap); keep seed/code, hide surface.
EVAL_UI_ENABLED = _env_bool("TOMO_EVAL_UI", default=False)