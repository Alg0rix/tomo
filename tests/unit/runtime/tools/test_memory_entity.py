"""Entity memory: self-pages refused, vault index listable, facts retrievable."""

import pytest

from app.runtime.tools import memory as memory_tool


@pytest.fixture
def home(tmp_path, monkeypatch):
    from app.core import home as home_mod

    monkeypatch.setattr(home_mod, "_root", lambda root=None: tmp_path)
    monkeypatch.setattr("app.runtime.tools.user_ctx.current_user_id", lambda: "usr_admin")
    return tmp_path


def test_refuses_page_for_the_user(home) -> None:
    out = memory_tool.run({"action": "add", "entity": "person/usr_admin",
                           "content": "Favorite F1 driver is Max."})
    assert out.startswith("Error:")


def test_list_without_key_indexes_vault(home) -> None:
    saved = memory_tool.run({"action": "add", "entity": "person/max-verstappen",
                             "content": "The user's favorite F1 driver."})
    assert saved == "Saved vault fact."
    out = memory_tool.run({"action": "list"})
    assert "[[person/max-verstappen]]" in out
    assert "favorite F1 driver" in out
