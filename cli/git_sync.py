"""Hermes-style git sync for managed Tomo installs."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class GitSyncResult:
    updated: bool
    commits: int
    head: str
    stash_ref: str | None
    used_hard_reset: bool


def _run(
    git_cmd: list[str],
    cwd: Path,
    args: list[str],
    *,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*git_cmd, *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def _stash_if_dirty(git_cmd: list[str], cwd: Path) -> str | None:
    status = _run(git_cmd, cwd, ["status", "--porcelain"])
    if status.returncode != 0:
        raise RuntimeError(status.stderr.strip() or "git status failed")
    if not status.stdout.strip():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    push = _run(
        git_cmd,
        cwd,
        ["stash", "push", "--include-untracked", "-m", f"tomo-update-autostash-{stamp}"],
    )
    if push.returncode != 0:
        raise RuntimeError(push.stderr.strip() or "git stash push failed")
    ref = _run(git_cmd, cwd, ["rev-parse", "--verify", "refs/stash"], check=True)
    return ref.stdout.strip()


def _restore_stash(
    git_cmd: list[str],
    cwd: Path,
    stash_ref: str,
    *,
    assume_yes: bool,
    input_fn: Callable[[str], str] | None,
) -> None:
    if not assume_yes:
        ask = input_fn or input
        try:
            answer = ask("Restore stashed local changes? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer not in {"", "y", "yes"}:
            print(f"Stash kept. Restore later with: git stash apply {stash_ref}")
            return
    apply = _run(git_cmd, cwd, ["stash", "apply", stash_ref])
    if apply.returncode != 0:
        print("⚠ Failed to restore stashed changes; stash left in place.")
        print(f"  Restore manually with: git stash apply {stash_ref}")
        if apply.stderr.strip():
            print(f"  {apply.stderr.strip()}")
        return
    # Drop matching stash entry if still present
    listed = _run(git_cmd, cwd, ["stash", "list", "--format=%gd %H"])
    for line in listed.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].strip() == stash_ref:
            _run(git_cmd, cwd, ["stash", "drop", parts[0]])
            break
    print("⚠ Local changes were restored on top of the updated codebase.")
    print("  Review `git diff` / `git status` if Tomo behaves unexpectedly.")


def sync_to_origin(
    cwd: Path,
    branch: str = "main",
    *,
    restore_stash: bool = True,
    assume_yes: bool = False,
    input_fn: Callable[[str], str] | None = None,
    git_cmd: list[str] | None = None,
) -> GitSyncResult:
    cwd = Path(cwd)
    git_cmd = list(git_cmd or ["git"])

    stash_ref = _stash_if_dirty(git_cmd, cwd)

    fetch = _run(git_cmd, cwd, ["fetch", "origin"])
    if fetch.returncode != 0:
        err = (fetch.stderr or fetch.stdout or "").strip()
        first = err.splitlines()[0] if err else "git fetch failed"
        if "Could not resolve host" in err or "unable to access" in err:
            raise RuntimeError(f"Network error — cannot reach remote: {first}")
        if "Authentication failed" in err or "could not read Username" in err:
            raise RuntimeError(f"Authentication failed: {first}")
        raise RuntimeError(first)

    current = _run(git_cmd, cwd, ["rev-parse", "--abbrev-ref", "HEAD"], check=True)
    current_branch = current.stdout.strip()
    if current_branch != branch:
        checkout = _run(git_cmd, cwd, ["checkout", "-B", branch, f"origin/{branch}"])
        if checkout.returncode != 0:
            checkout = _run(git_cmd, cwd, ["checkout", branch])
            if checkout.returncode != 0:
                raise RuntimeError(
                    checkout.stderr.strip() or f"failed to checkout {branch}"
                )

    count = _run(
        git_cmd,
        cwd,
        ["rev-list", f"HEAD..origin/{branch}", "--count"],
        check=True,
    )
    commits = int(count.stdout.strip() or "0")
    head = _run(git_cmd, cwd, ["rev-parse", "--short", "HEAD"], check=True).stdout.strip()

    if commits == 0:
        if restore_stash and stash_ref is not None:
            _restore_stash(
                git_cmd,
                cwd,
                stash_ref,
                assume_yes=assume_yes,
                input_fn=input_fn,
            )
        return GitSyncResult(
            updated=False,
            commits=0,
            head=head,
            stash_ref=stash_ref,
            used_hard_reset=False,
        )

    used_hard_reset = False
    pull = _run(git_cmd, cwd, ["pull", "--ff-only", "origin", branch])
    if pull.returncode != 0:
        reset = _run(git_cmd, cwd, ["reset", "--hard", f"origin/{branch}"])
        if reset.returncode != 0:
            raise RuntimeError(
                reset.stderr.strip()
                or f"Failed to reset to origin/{branch}. Try: git fetch origin && git reset --hard origin/{branch}"
            )
        used_hard_reset = True

    if restore_stash and stash_ref is not None:
        _restore_stash(
            git_cmd,
            cwd,
            stash_ref,
            assume_yes=assume_yes,
            input_fn=input_fn,
        )

    head = _run(git_cmd, cwd, ["rev-parse", "--short", "HEAD"], check=True).stdout.strip()
    return GitSyncResult(
        updated=True,
        commits=commits,
        head=head,
        stash_ref=stash_ref,
        used_hard_reset=used_hard_reset,
    )
