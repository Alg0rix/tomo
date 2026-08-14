"""Pytest fixtures and test configuration.

The Tomo Home root (``$TOMO_HOME``) and the SQLite DB path are forced onto temp
directories BEFORE ``app.core.config`` is imported, so the module-level
``store = Store()`` singleton never touches the developer's real ``~/.tomo`` or
``var/tomo.db``. Individual tests rebind the store to their own ``tmp_path``
for isolation. ``TOMO_SECRET_KEY`` is intentionally left unset so tests
exercise the ``$TOMO_HOME/.secret_key`` path (auto-created on first use).

Empty-DB ``store.rebind`` is expensive (migrate + seed + scrypt). After the
first seeded DB exists we snapshot it and copy that file on later rebinds
instead of rebuilding from scratch.
"""
import os
import shutil
import tempfile
from pathlib import Path

_TEST_HOME = tempfile.mkdtemp(prefix="tomo-home-pytest-")
_TEST_WORK = tempfile.mkdtemp(prefix="tomo-work-pytest-")
os.environ["TOMO_HOME"] = _TEST_HOME
os.environ["TOMO_WORK"] = _TEST_WORK
# Force, don't setdefault: xdist workers inherit the controller env, and a
# leftover TOMO_DB_PATH would point at the controller's already-seeded DB.
os.environ["TOMO_DB_PATH"] = os.path.join(_TEST_HOME, "state", "tomo.db")
# Do not pull the developer's ~/.agents/skills into unit tests.
os.environ["TOMO_SKILLS_EXTERNAL_DIRS"] = ""
# Skip SQLite fsync in tests (see app.models.db.get_connection).
os.environ["TOMO_TEST_FAST_SQLITE"] = "1"


def pytest_configure(config) -> None:
    """Snapshot the first seeded DB and clone it on later ``Store.rebind``."""
    from app.services.store import Store, store

    template = Path(tempfile.mkdtemp(prefix="tomo-db-template-")) / "template.db"
    store._conn.execute("VACUUM INTO ?", (str(template),))

    original = Store.rebind

    def _rebind_from_template(self, path):
        if path is not None:
            dest = Path(path)
            if not dest.exists() and template.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(template, dest)
        return original(self, path)

    Store.rebind = _rebind_from_template
    # Keep a handle so xdist workers / debugging can find the snapshot.
    config._tomo_db_template = template


def pytest_collection_modifyitems(items) -> None:
    """Mark tests under tests/integration/ (subdirectory hooks see all items)."""
    import pytest

    marker = pytest.mark.integration
    for item in items:
        if "integration" in item.path.parts:
            item.add_marker(marker)
