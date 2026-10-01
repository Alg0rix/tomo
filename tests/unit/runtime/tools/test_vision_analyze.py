"""vision_analyze tool — source resolution and describe routing."""

from __future__ import annotations

import base64

import pytest

import app.runtime.llm.vision as llm_vision
from app.runtime.tools import vision_analyze
from app.services import store

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
    monkeypatch.setattr(
        store,
        "get_attachment",
        lambda aid: {
            "id": aid,
            "original_name": "shot.png",
            "mime_type": "image/png",
            "file_path": str(p),
        },
    )
    out = vision_analyze.run({"source": "attachment:a1", "question": "what is it?"})
    assert "red pixel" in out
    assert fake_analyze and fake_analyze[0][0].startswith("data:image/")


def test_data_url_source(fake_analyze) -> None:
    src = "data:image/png;base64," + base64.b64encode(_PNG).decode()
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
    out = vision_analyze.run({"source": "img.png", "question": "what?"})
    assert "red pixel" in out


def test_local_path_escape_rejected(monkeypatch, tmp_path) -> None:
    from app.runtime.tools import sandbox

    root = tmp_path / "work"
    root.mkdir()
    monkeypatch.setattr(
        "app.runtime.tools.sandbox.resolve_work_root", lambda agent_id=None: root
    )
    out = vision_analyze.run({"source": "../etc/passwd", "question": "x"})
    assert out.startswith("Error:")


def test_http_url_blocked_for_private_hosts() -> None:
    out = vision_analyze.run(
        {"source": "http://localhost:9999/x.png", "question": "x"}
    )
    assert "blocked" in out or out.startswith("Error:")


def test_region_requires_four_ints() -> None:
    out = vision_analyze.run({"source": "attachment:a1", "question": "x", "region": [0, 0]})
    assert "region" in out


def test_missing_source() -> None:
    assert vision_analyze.run({"question": "x"}).startswith("Error:")


def test_analysis_error_surfaces(monkeypatch, tmp_path) -> None:
    p = tmp_path / "shot.png"
    p.write_bytes(_PNG)
    monkeypatch.setattr(
        store,
        "get_attachment",
        lambda aid: {"id": aid, "mime_type": "image/png", "file_path": str(p)},
    )

    async def _boom(agent_id, data_url, question=""):
        raise RuntimeError("no vision profile")

    monkeypatch.setattr(llm_vision, "analyze_image_data_url", _boom)
    out = vision_analyze.run({"source": "attachment:a1", "question": "x"})
    assert "vision analysis failed" in out


def test_oversize_data_url_refused() -> None:
    big = "data:image/png;base64," + base64.b64encode(b"x" * (21 * 1024 * 1024)).decode()
    out = vision_analyze.run({"source": big, "question": "x"})
    assert "too large" in out
