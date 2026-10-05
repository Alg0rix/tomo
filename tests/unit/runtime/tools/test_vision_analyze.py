"""vision_analyze tool — source resolution and describe routing.

Every test binds an explicit owned execution (Admin or restricted Member)
with an honestly assigned vision-capable profile. The ``analyze`` seam is a
test double for the *external model provider only* — identity, ceiling,
attachment ownership, path jailing and profile assignment are all real.
"""

from __future__ import annotations

import base64

import pytest

import app.runtime.llm.vision as llm_vision
from app.runtime.tools import vision_analyze
from app.services import store
from tests.fakes.access import admin_with_vision_profile, restricted_member_with_egress

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def fake_analyze(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def _fake(agent_id, data_url, question=""):
        calls.append((data_url, question))
        return "the image shows a red pixel"

    monkeypatch.setattr(llm_vision, "analyze_image_data_url", _fake)
    return calls


def test_attachment_source(monkeypatch, tmp_path, fake_analyze) -> None:
    p = tmp_path / "shot.png"
    p.write_bytes(_PNG)
    with admin_with_vision_profile() as (context, _root, _profile):
        # The row must belong to the bound chat: foreign session ids stay
        # invisible (see the cross-chat test below).
        monkeypatch.setattr(
            store,
            "get_attachment",
            lambda aid: {
                "id": aid,
                "session_id": context.session_id,
                "original_name": "shot.png",
                "mime_type": "image/png",
                "file_path": str(p),
            },
        )
        out = vision_analyze.run({"source": "attachment:a1", "question": "what is it?"})
    assert "red pixel" in out
    assert fake_analyze and fake_analyze[0][0].startswith("data:image/")


def test_attachment_from_another_chat_is_invisible(monkeypatch, tmp_path, fake_analyze) -> None:
    p = tmp_path / "shot.png"
    p.write_bytes(_PNG)
    with admin_with_vision_profile() as (context, _root, _profile):
        monkeypatch.setattr(
            store,
            "get_attachment",
            lambda aid: {
                "id": aid,
                "session_id": "someone-elses-chat",
                "original_name": "shot.png",
                "mime_type": "image/png",
                "file_path": str(p),
            },
        )
        out = vision_analyze.run({"source": "attachment:a1", "question": "what is it?"})
    assert out.startswith("Error:")
    assert "not found" in out
    assert not fake_analyze


def test_data_url_source(fake_analyze) -> None:
    src = "data:image/png;base64," + base64.b64encode(_PNG).decode()
    with admin_with_vision_profile():
        out = vision_analyze.run({"source": src, "question": "describe"})
    assert "red pixel" in out


def test_restricted_member_data_url_source(fake_analyze) -> None:
    # Restricted Members describe inline bytes through their assigned vision
    # profile — no host paths involved.
    src = "data:image/png;base64," + base64.b64encode(_PNG).decode()
    with restricted_member_with_egress():
        out = vision_analyze.run({"source": src, "question": "describe"})
    assert "red pixel" in out


def test_local_path_source(monkeypatch, tmp_path, fake_analyze) -> None:
    from app.runtime.tools import sandbox

    root = tmp_path / "work"
    root.mkdir()
    (root / "img.png").write_bytes(_PNG)
    monkeypatch.setattr(sandbox, "resolve_work_root", lambda agent_id=None: root)
    # vision_analyze imported the name lazily inside _local_path_bytes — patch
    # the module it resolves from.
    monkeypatch.setattr(
        "app.runtime.tools.sandbox.resolve_work_root", lambda agent_id=None: root
    )
    with admin_with_vision_profile():
        out = vision_analyze.run({"source": "img.png", "question": "what?"})
    assert "red pixel" in out


def test_local_path_escape_rejected() -> None:
    # Restricted execution: a symlink/path escape from the owned workplace is
    # rejected by containment, not followed. (Unrestricted Admin hosts are
    # governed by the OS account by design — see the design doc.)
    with restricted_member_with_egress() as (context, _sid):
        from pathlib import Path

        root = Path(context.resources[0].root_path)
        outside = root.parent / "outside.txt"
        outside.write_text("secret")
        link = root / "link.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            pass
        assert vision_analyze.run({"source": "../outside.txt", "question": "x"}).startswith("Error:")
        if link.is_symlink():
            assert vision_analyze.run({"source": "link.txt", "question": "x"}).startswith("Error:")


def test_http_url_blocked_for_private_hosts() -> None:
    # Scoped egress is on, but private hosts stay blocked by the SSRF guard.
    with admin_with_vision_profile(egress="scoped"):
        out = vision_analyze.run(
            {"source": "http://localhost:9999/x.png", "question": "x"}
        )
    assert "blocked" in out or out.startswith("Error:")


def test_http_url_denied_without_egress() -> None:
    # Default deny: egress off fails closed before any DNS or download.
    # Direct-call authorization denials raise (fail-closed); the registry
    # maps them to "Error:" strings with a denied audit row.
    import pytest

    from app.runtime.access import AccessDenied
    from app.runtime.tools.registry import execute

    with admin_with_vision_profile():
        with pytest.raises(AccessDenied):
            vision_analyze.run(
                {"source": "https://example.com/x.png", "question": "x"}
            )
        out = execute("vision_analyze", {"source": "https://example.com/x.png", "question": "x"})
    assert out.startswith("Error:")
    assert "egress" in out.lower() or "disabled" in out.lower()


def test_region_requires_four_ints() -> None:
    with admin_with_vision_profile():
        out = vision_analyze.run({"source": "attachment:a1", "question": "x", "region": [0, 0]})
    assert "region" in out


def test_missing_source() -> None:
    with admin_with_vision_profile():
        assert vision_analyze.run({"question": "x"}).startswith("Error:")


def test_analysis_error_surfaces(monkeypatch, tmp_path) -> None:
    p = tmp_path / "shot.png"
    p.write_bytes(_PNG)
    with admin_with_vision_profile() as (context, _root, _profile):
        monkeypatch.setattr(
            store,
            "get_attachment",
            lambda aid: {
                "id": aid,
                "session_id": context.session_id,
                "mime_type": "image/png",
                "file_path": str(p),
            },
        )

        async def _boom(agent_id, data_url, question=""):
            raise RuntimeError("no vision profile")

        monkeypatch.setattr(llm_vision, "analyze_image_data_url", _boom)
        out = vision_analyze.run({"source": "attachment:a1", "question": "x"})
    assert "vision analysis failed" in out


def test_oversize_data_url_refused() -> None:
    big = "data:image/png;base64," + base64.b64encode(b"x" * (21 * 1024 * 1024)).decode()
    with admin_with_vision_profile():
        out = vision_analyze.run({"source": big, "question": "x"})
    assert "too large" in out
