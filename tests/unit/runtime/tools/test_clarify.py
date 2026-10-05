"""clarify tool tests — registry validation path."""

from __future__ import annotations

import pytest

from app.runtime.tools.registry import execute, reset_registry
from app.services import store
from tests.fakes.access import owned_admin_scope


@pytest.fixture(autouse=True)
def _reset(tmp_path) -> None:
    store.rebind(tmp_path / "clarify.db")
    reset_registry()
    yield
    reset_registry()


@pytest.fixture()
def admin_fs():
    # Explicit owned Admin unrestricted context (real policy grant + ack).
    # Host tools are fenced to the session workplace root, not TOMO_WORK.
    with owned_admin_scope() as (_ctx, root):
        yield root


def test_clarify_empty_is_error(admin_fs) -> None:
    assert execute("clarify", {"question": "  "}).startswith("Error")




def test_clarify_direct_execute_points_at_loop(admin_fs) -> None:
    result = execute("clarify", {"question": "Which environment?"})
    assert result.startswith("Error: clarify must be handled")
