"""Portal path helpers and local↔portal file bridge."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.core import config
from app.runtime.portal import paths, transfers
from app.runtime.portal.io import Location, copy_sync, parse_location
from app.runtime.tools import portal as portal_tool
from app.runtime.tools import sandbox
from app.runtime.tools.registry import execute, get_openai_tools, reset_registry
from app.services import store


def _rebind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("TOMO_WORK", str(work))
    monkeypatch.setattr(config, "TOMO_WORK", work)
    store.rebind(tmp_path / "portal.db")
    reset_registry()
    sandbox.reset_agent()
    transfers.reset()
    return work


def test_parse_portal_path() -> None:
    assert paths.parse_portal_path("/_portal/edge/a/b.bin") == ("edge", "a/b.bin")
    with pytest.raises(ValueError):
        paths.parse_portal_path("/_portal/")
    with pytest.raises(ValueError):
        paths.parse_portal_path("/_portal/../x")


def test_resolve_portal_fs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = _rebind(tmp_path, monkeypatch)
    p = paths.resolve_portal_fs("/_portal/build/out.bin", create=True)
    assert p == (work / "_portal" / "build" / "out.bin").resolve()
    assert p.parent.is_dir()


def test_copy_local_to_portal_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = _rebind(tmp_path, monkeypatch)
    root = tmp_path / "device"
    root.mkdir()
    src_file = root / "artifact.bin"
    payload = b"portal-payload-" + (b"x" * 200)
    src_file.write_bytes(payload)

    wp = store.create_workplace(
        {
            "id": "wp_dev",
            "name": "DevBox",
            "kind": "local",
            "root_path": str(root),
        }
    )
    assert wp["id"] == "wp_dev"

    out = portal_tool.run(
        {
            "action": "copy",
            "src": "wp_dev:artifact.bin",
            "dst": "/_portal/edge/artifact.bin",
        }
    )
    assert out.startswith("Copied")
    dest = work / "_portal" / "edge" / "artifact.bin"
    assert dest.read_bytes() == payload


def test_copy_portal_to_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = _rebind(tmp_path, monkeypatch)
    staged = work / "_portal" / "cfg" / "app.toml"
    staged.parent.mkdir(parents=True)
    staged.write_text("mode = 'edge'\n", encoding="utf-8")

    root = tmp_path / "node"
    root.mkdir()
    store.create_workplace(
        {"id": "wp_node", "name": "Node", "kind": "local", "root_path": str(root)}
    )

    out = portal_tool.run(
        {
            "action": "copy",
            "src": "/_portal/cfg/app.toml",
            "dst": "wp_node:etc/app.toml",
        }
    )
    assert "Copied" in out
    assert (root / "etc" / "app.toml").read_text(encoding="utf-8") == "mode = 'edge'\n"


def test_async_transfer_and_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = _rebind(tmp_path, monkeypatch)
    monkeypatch.setattr(transfers, "SYNC_MAX_BYTES", 64)

    root = tmp_path / "big"
    root.mkdir()
    blob = b"Z" * 2000
    (root / "big.bin").write_bytes(blob)
    store.create_workplace(
        {"id": "wp_big", "name": "Big", "kind": "local", "root_path": str(root)}
    )

    out = portal_tool.run(
        {
            "action": "copy",
            "src": "wp_big:big.bin",
            "dst": "/_portal/cache/big.bin",
        }
    )
    assert "Started transfer" in out
    job_id = out.split()[2].rstrip(":")
    deadline = time.time() + 5
    status = ""
    while time.time() < deadline:
        status = portal_tool.run({"action": "status", "id": job_id})
        if "status: done" in status:
            break
        time.sleep(0.05)
    assert "status: done" in status
    assert (work / "_portal" / "cache" / "big.bin").read_bytes() == blob


def test_parse_location_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rebind(tmp_path, monkeypatch)
    root = tmp_path / "r"
    root.mkdir()
    store.create_workplace(
        {"id": "wp_x", "name": "EdgeNode", "kind": "local", "root_path": str(root)}
    )
    loc = parse_location("EdgeNode:rel/path.txt")
    assert loc.kind == "local"
    assert loc.workplace_id == "wp_x"
    assert loc.path == "rel/path.txt"


def test_portal_registry_schema() -> None:
    tools = get_openai_tools()
    portal = next(t for t in tools if t["function"]["name"] == "portal")
    assert "action" in portal["function"]["parameters"]["properties"]
    assert execute("portal", {"action": "list"}).startswith("Portals:")


def test_copy_sync_direct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = _rebind(tmp_path, monkeypatch)
    src_path = work / "_portal" / "a" / "in.bin"
    src_path.parent.mkdir(parents=True)
    src_path.write_bytes(b"abc123")
    dst = Location(kind="portal", path="/_portal/b/out.bin")
    src = Location(kind="portal", path="/_portal/a/in.bin")
    n = copy_sync(src, dst)
    assert n == 6
    assert (work / "_portal" / "b" / "out.bin").read_bytes() == b"abc123"
