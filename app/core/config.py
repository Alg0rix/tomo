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


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


# --- Server ---
HOST = os.environ.get("TOMO_HOST", "127.0.0.1")
PORT = int(os.environ.get("TOMO_PORT", "8787"))
RELOAD = _env_bool("TOMO_RELOAD", default=False)

# --- Auth ---
SECRET_KEY = os.environ.get("TOMO_SECRET_KEY", "tomo-dev-secret-change-me")
ADMIN_PASSWORD = os.environ.get("TOMO_ADMIN_PASSWORD", "tomo")
SESSION_COOKIE_NAME = "tomo_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

# --- Brand ---
BRAND = "Tomo"
BRAND_SUBTITLE = "友達 · agent swarm"
