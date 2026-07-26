"""Pytest fixtures and test configuration.

The foundation SQLite DB path is forced onto a temp directory BEFORE
``app.core.config`` is imported, so the module-level ``store = Store()``
singleton never touches the production ``var/tomo.db``. Individual tests
rebind the store to their own ``tmp_path`` for isolation.
"""
import os
import tempfile

_TEST_VAR = tempfile.mkdtemp(prefix="tomo-pytest-")
os.environ["TOMO_DB_PATH"] = os.path.join(_TEST_VAR, "tomo.db")
