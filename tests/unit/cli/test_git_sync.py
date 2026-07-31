from __future__ import annotations

import subprocess
from pathlib import Path

from cli.git_sync import sync_to_origin


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return r.stdout.strip()


def _init_repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    local = tmp_path / "local"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "clone", str(remote), str(local))
    _git(local, "checkout", "-b", "main")
    (local / "README").write_text("v1\n", encoding="utf-8")
    _git(local, "add", "README")
    _git(local, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "v1")
    _git(local, "push", "-u", "origin", "main")
    return local, remote


def test_sync_already_up_to_date(tmp_path: Path) -> None:
    local, _ = _init_repo_with_remote(tmp_path)
    result = sync_to_origin(local, "main", assume_yes=True)
    assert result.updated is False
    assert result.commits == 0


def test_sync_fast_forward(tmp_path: Path) -> None:
    local, remote = _init_repo_with_remote(tmp_path)
    other = tmp_path / "other"
    _git(tmp_path, "clone", str(remote), str(other))
    (other / "README").write_text("v2\n", encoding="utf-8")
    _git(other, "add", "README")
    _git(other, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "v2")
    _git(other, "push", "origin", "main")

    result = sync_to_origin(local, "main", assume_yes=True)
    assert result.updated is True
    assert result.commits >= 1
    assert (local / "README").read_text(encoding="utf-8") == "v2\n"
    assert result.used_hard_reset is False


def test_sync_hard_reset_when_diverged(tmp_path: Path) -> None:
    local, remote = _init_repo_with_remote(tmp_path)
    (local / "README").write_text("local\n", encoding="utf-8")
    _git(local, "add", "README")
    _git(local, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "local")
    other = tmp_path / "other"
    _git(tmp_path, "clone", str(remote), str(other))
    (other / "README").write_text("remote\n", encoding="utf-8")
    _git(other, "add", "README")
    _git(other, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "remote")
    _git(other, "push", "-f", "origin", "main")

    result = sync_to_origin(local, "main", assume_yes=True)
    assert result.updated is True
    assert result.used_hard_reset is True
    assert (local / "README").read_text(encoding="utf-8") == "remote\n"


def test_sync_stashes_dirty_tree(tmp_path: Path) -> None:
    local, remote = _init_repo_with_remote(tmp_path)
    other = tmp_path / "other"
    _git(tmp_path, "clone", str(remote), str(other))
    (other / "README").write_text("v2\n", encoding="utf-8")
    _git(other, "add", "README")
    _git(other, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "v2")
    _git(other, "push", "origin", "main")

    (local / "dirty.txt").write_text("x\n", encoding="utf-8")
    result = sync_to_origin(local, "main", assume_yes=True, restore_stash=True)
    assert result.updated is True
    assert (local / "dirty.txt").exists()
