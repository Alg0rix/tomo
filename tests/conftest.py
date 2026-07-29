"""Pytest fixtures and test configuration.

The Tomo Home root (``$TOMO_HOME``) and the SQLite DB path are forced onto temp
directories BEFORE ``app.core.config`` is imported, so the module-level
``store = Store()`` singleton never touches the developer's real ``~/.tomo`` or
``var/tomo.db``. Individual tests rebind the store to their own ``tmp_path``
for isolation. ``TOMO_SECRET_KEY`` is intentionally left unset so tests
exercise the ``$TOMO_HOME/.secret_key`` path (auto-created on first use).
"""
import os
import tempfile

_TEST_HOME = tempfile.mkdtemp(prefix="tomo-home-pytest-")
_TEST_WORK = tempfile.mkdtemp(prefix="tomo-work-pytest-")
os.environ["TOMO_HOME"] = _TEST_HOME
os.environ["TOMO_WORK"] = _TEST_WORK
os.environ.setdefault("TOMO_DB_PATH", os.path.join(_TEST_HOME, "state", "tomo.db"))
