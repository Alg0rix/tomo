"""A bare "swarm" message opts the previous request into a swarm run."""

import app.main  # noqa: F401  (resolve import cycle)
from app.channels import web


def test_bare_optin_phrases() -> None:
    for msg in ("swarm", "Swarm!", "pakai  swarm", "/swarm", "use swarm."):
        assert web._is_bare_swarm_optin(msg), msg
    for msg in ("swarm research the news", "team", "what is a swarm?", ""):
        assert not web._is_bare_swarm_optin(msg), msg


def test_previous_user_request_skips_optins(monkeypatch) -> None:
    history = [
        {"type": "user", "content": "cari berita politik hari ini"},
        {"type": "final", "content": "..."},
        {"type": "user", "content": "swarm"},
    ]
    monkeypatch.setattr(web.store, "get_session_history", lambda sid: history)
    assert web._previous_user_request("ses_x") == "cari berita politik hari ini"
