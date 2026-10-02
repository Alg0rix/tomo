"""Image-input preparation: mode routing + auxiliary-vision descriptions."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

import app.runtime.llm.vision as llm_vision
import app.services.vision as svc_vision
from app.services import store
from app.services.chat import attachment_info_lines

# 1x1 red PNG.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """Isolate the description cache per test (keys are content hashes)."""
    target = tmp_path / "vision_descriptions.json"
    monkeypatch.setattr(svc_vision, "_cache_path", lambda: target)
    return target


_ATTACHMENTS: dict[str, dict] = {}


@pytest.fixture(autouse=True)
def _attachments(monkeypatch):
    """Attachments without a session FK — stub the store read."""
    _ATTACHMENTS.clear()
    monkeypatch.setattr(
        store, "get_attachment", lambda aid: _ATTACHMENTS.get(aid)
    )


def _attach(tmp_path, att_id: str, raw: bytes = _PNG, name: str = "img.png") -> dict:
    p = tmp_path / f"{att_id}.png"
    p.write_bytes(raw)
    _ATTACHMENTS[att_id] = {
        "id": att_id,
        "session_id": "s1",
        "filename": name,
        "original_name": name,
        "mime_type": "image/png",
        "size_bytes": len(raw),
        "file_path": str(p),
    }
    return _ATTACHMENTS[att_id]


def _history(*att_ids: str) -> list[dict]:
    return [
        {"type": "user", "content": "look at this", "attachment_ids": list(att_ids)}
    ]


@pytest.mark.asyncio
async def test_no_images_skips_lookups(tmp_path, monkeypatch) -> None:
    store.rebind(tmp_path / "v.db")
    monkeypatch.setattr(
        llm_vision,
        "decide_image_input_mode",
        lambda *a, **kw: pytest.fail("must not run without images"),
    )
    plan = await svc_vision.prepare_image_inputs(
        [{"type": "user", "content": "hi"}], None
    )
    assert plan == {"mode": "text", "descriptions": {}}


@pytest.mark.asyncio
async def test_native_mode_never_describes(tmp_path, monkeypatch, cache_dir) -> None:
    store.rebind(tmp_path / "v.db")
    att = _attach(tmp_path, "a1")
    monkeypatch.setattr(llm_vision, "decide_image_input_mode", lambda *a, **kw: "native")
    monkeypatch.setattr(
        llm_vision,
        "analyze_image_data_url",
        lambda *a, **kw: pytest.fail("native mode must not describe"),
    )
    plan = await svc_vision.prepare_image_inputs(_history("a1"), None)
    assert plan == {"mode": "native", "descriptions": {}}
    assert att["id"] == "a1"


@pytest.mark.asyncio
async def test_text_mode_describes_and_caches(tmp_path, monkeypatch, cache_dir) -> None:
    store.rebind(tmp_path / "v.db")
    _attach(tmp_path, "a1")
    monkeypatch.setattr(llm_vision, "decide_image_input_mode", lambda *a, **kw: "text")
    monkeypatch.setattr(
        llm_vision, "resolve_vision_profile", lambda *a, **kw: {"id": "vis"}
    )
    calls: list[str] = []

    async def _fake_analyze(agent_id, data_url, question=""):
        calls.append(data_url)
        return "a red pixel"

    monkeypatch.setattr(llm_vision, "analyze_image_data_url", _fake_analyze)
    history = _history("a1")
    plan = await svc_vision.prepare_image_inputs(history, None)
    assert plan["mode"] == "text"
    assert plan["descriptions"] == {"a1": "a red pixel"}
    assert len(calls) == 1
    # Second turn over the same history serves the disk cache — no re-call.
    plan = await svc_vision.prepare_image_inputs(history, None)
    assert plan["descriptions"] == {"a1": "a red pixel"}
    assert len(calls) == 1
    # Cache file keyed by content hash exists.
    key = hashlib.sha256(_PNG).hexdigest()
    cache = json.loads(cache_dir.read_text())
    assert cache[key]["text"] == "a red pixel"


@pytest.mark.asyncio
async def test_text_mode_without_vision_profile(tmp_path, monkeypatch, cache_dir) -> None:
    store.rebind(tmp_path / "v.db")
    _attach(tmp_path, "a1")
    monkeypatch.setattr(llm_vision, "decide_image_input_mode", lambda *a, **kw: "text")
    monkeypatch.setattr(llm_vision, "resolve_vision_profile", lambda *a, **kw: None)
    plan = await svc_vision.prepare_image_inputs(_history("a1"), None)
    assert plan == {"mode": "text", "descriptions": {}}


@pytest.mark.asyncio
async def test_describe_failure_drops_image_not_turn(
    tmp_path, monkeypatch, cache_dir
) -> None:
    store.rebind(tmp_path / "v.db")
    _attach(tmp_path, "a1")
    _attach(tmp_path, "a2")
    monkeypatch.setattr(llm_vision, "decide_image_input_mode", lambda *a, **kw: "text")
    monkeypatch.setattr(
        llm_vision, "resolve_vision_profile", lambda *a, **kw: {"id": "vis"}
    )

    async def _flaky(agent_id, data_url, question=""):
        # a1 and a2 share content → same cache key; force failure once.
        raise RuntimeError("backend down")

    monkeypatch.setattr(llm_vision, "analyze_image_data_url", _flaky)
    plan = await svc_vision.prepare_image_inputs(_history("a1", "a2"), None)
    assert plan["descriptions"] == {}


def test_attachment_info_lines_injects_description(tmp_path) -> None:
    store.rebind(tmp_path / "v.db")
    _attach(tmp_path, "a1")
    text = attachment_info_lines(
        ["a1"], image_descriptions={"a1": "a red pixel"}
    )
    assert "a red pixel" in text
    assert "vision model" in text


def test_attachment_info_lines_native_skips_description(tmp_path) -> None:
    store.rebind(tmp_path / "v.db")
    _attach(tmp_path, "a1")
    text = attachment_info_lines(
        ["a1"], vision_capable=True, image_descriptions={"a1": "unused"}
    )
    assert "image content included below" in text
    assert "unused" not in text
