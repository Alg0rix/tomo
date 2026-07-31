# Git install / update + systemd user service — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bootstrap Tomo from git via `scripts/install.sh`, manage updates/uninstall via CLI, and run as a systemd **user** unit with `TOMO_HOME` and `TOMO_WORK` set explicitly.

**Architecture:** Fixed managed install at `~/.local/share/tomo/app`. Shell script handles first clone + unit write + enable. Python CLI (`cli/`) owns post-install `update`, `uninstall`, and `service` subcommands, sharing path/unit helpers. Git sync follows Hermes: autostash → ff-only → hard-reset fallback.

**Tech Stack:** bash, git, uv, systemd `--user`, Python argparse stdlib, pytest.

**Spec:** `docs/superpowers/specs/2026-07-31-git-install-systemd-user-design.md`

## Global Constraints

- Code install path is exactly `~/.local/share/tomo/app` (XDG share).
- Unit **must** set `Environment=TOMO_HOME=%h/.tomo` and `Environment=TOMO_WORK=%h/tomo`.
- There is no `TOMO_WORKDIR`; use `TOMO_WORK` only.
- User-scope systemd only — never system-wide / root unit.
- Uninstall default keeps data; `--purge` deletes `$TOMO_HOME` and `$TOMO_WORK` after confirm (or `-y`).
- Do not start/stop/kill the user's live Tomo server during implementation testing unless using an isolated temp HOME (workspace rule: do not control the Tomo app server).
- Track branch default: `main`. Store chosen branch in `$INSTALL_DIR/.tomo-install-branch` (one line) so update/reinstall stay consistent.

## File map

| File | Responsibility |
|------|----------------|
| `cli/paths.py` | Resolve install/home/work/bin/unit paths |
| `cli/unit.py` | Render `tomo.service` text; parse `TOMO_*` from an existing unit |
| `cli/git_sync.py` | Autostash + fetch + ff-only / hard-reset + restore |
| `cli/service.py` | `systemctl --user` wrappers |
| `cli/update_cmd.py` | `tomo update` orchestration |
| `cli/uninstall_cmd.py` | `tomo uninstall` / `--purge` |
| `cli/__main__.py` | argparse dispatch |
| `cli/commands.py` | Keep or thin-reexport (avoid empty stub confusion) |
| `scripts/install.sh` | Bootstrap clone/sync, uv sync, write unit, enable/start, symlink |
| `scripts/tomo.service.in` | Optional template copied by install.sh (or embed heredoc) |
| `tests/unit/cli/test_paths.py` | Path helpers |
| `tests/unit/cli/test_git_sync.py` | Temp-repo sync behavior |
| `tests/unit/cli/test_uninstall.py` | Default vs purge path selection (mocked systemctl) |
| `tests/unit/cli/test_unit.py` | Unit file contains required env lines |
| `README.md` | Install-as-user-service section |
| `scripts/README.md` | Point at install.sh |

---

### Task 1: Path helpers

**Files:**
- Create: `cli/paths.py`
- Create: `tests/unit/cli/__init__.py` (empty)
- Create: `tests/unit/cli/test_paths.py`

**Interfaces:**
- Produces:
  - `install_dir(home: Path | None = None) -> Path` → `{home}/.local/share/tomo/app`
  - `unit_path(home: Path | None = None) -> Path` → `{home}/.config/systemd/user/tomo.service`
  - `cli_symlink_path(home: Path | None = None) -> Path` → `{home}/.local/bin/tomo`
  - `default_tomo_home(home: Path | None = None) -> Path` → `{home}/.tomo`
  - `default_tomo_work(home: Path | None = None) -> Path` → `{home}/tomo`
  - `branch_marker_path(install: Path) -> Path` → `install / ".tomo-install-branch"`
  - `read_tracked_branch(install: Path, default: str = "main") -> str`
  - `write_tracked_branch(install: Path, branch: str) -> None`
- Consumes: none

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/cli/test_paths.py
from pathlib import Path

from cli.paths import (
    cli_symlink_path,
    default_tomo_home,
    default_tomo_work,
    install_dir,
    read_tracked_branch,
    unit_path,
    write_tracked_branch,
)


def test_paths_under_fake_home(tmp_path: Path) -> None:
    assert install_dir(tmp_path) == tmp_path / ".local/share/tomo/app"
    assert unit_path(tmp_path) == tmp_path / ".config/systemd/user/tomo.service"
    assert cli_symlink_path(tmp_path) == tmp_path / ".local/bin/tomo"
    assert default_tomo_home(tmp_path) == tmp_path / ".tomo"
    assert default_tomo_work(tmp_path) == tmp_path / "tomo"


def test_tracked_branch_roundtrip(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    assert read_tracked_branch(app) == "main"
    write_tracked_branch(app, "develop")
    assert read_tracked_branch(app) == "develop"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/cli/test_paths.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'cli.paths'` (or import error)

- [ ] **Step 3: Write minimal implementation**

```python
# cli/paths.py
"""Managed-install path helpers (systemd user install layout)."""

from __future__ import annotations

from pathlib import Path

UNIT_NAME = "tomo.service"
REPO_HTTPS = "https://github.com/Alg0rix/tomo.git"
DEFAULT_BRANCH = "main"


def _home(home: Path | None) -> Path:
    return Path.home() if home is None else Path(home)


def install_dir(home: Path | None = None) -> Path:
    return _home(home) / ".local" / "share" / "tomo" / "app"


def unit_path(home: Path | None = None) -> Path:
    return _home(home) / ".config" / "systemd" / "user" / UNIT_NAME


def cli_symlink_path(home: Path | None = None) -> Path:
    return _home(home) / ".local" / "bin" / "tomo"


def default_tomo_home(home: Path | None = None) -> Path:
    return _home(home) / ".tomo"


def default_tomo_work(home: Path | None = None) -> Path:
    return _home(home) / "tomo"


def branch_marker_path(install: Path) -> Path:
    return Path(install) / ".tomo-install-branch"


def read_tracked_branch(install: Path, default: str = DEFAULT_BRANCH) -> str:
    marker = branch_marker_path(install)
    if not marker.is_file():
        return default
    text = marker.read_text(encoding="utf-8").strip()
    return text or default


def write_tracked_branch(install: Path, branch: str) -> None:
    branch_marker_path(install).write_text(branch.strip() + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/cli/test_paths.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cli/paths.py tests/unit/cli/__init__.py tests/unit/cli/test_paths.py
git commit -m "$(cat <<'EOF'
feat(cli): add managed install path helpers

EOF
)"
```

---

### Task 2: Unit file renderer + parser

**Files:**
- Create: `cli/unit.py`
- Create: `tests/unit/cli/test_unit.py`

**Interfaces:**
- Produces:
  - `render_user_unit() -> str` — full unit text using `%h` paths from the spec
  - `parse_tomo_paths_from_unit(text: str, home: Path) -> tuple[Path, Path]` — expand `%h` / `$HOME`; fall back to defaults if missing
- Consumes: `cli.paths.default_tomo_home`, `default_tomo_work`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/cli/test_unit.py
from pathlib import Path

from cli.unit import parse_tomo_paths_from_unit, render_user_unit


def test_render_includes_required_env() -> None:
    text = render_user_unit()
    assert "Environment=TOMO_HOME=%h/.tomo" in text
    assert "Environment=TOMO_WORK=%h/tomo" in text
    assert "WorkingDirectory=%h/.local/share/tomo/app" in text
    assert "ExecStart=%h/.local/share/tomo/app/.venv/bin/python -m app.main" in text
    assert "WantedBy=default.target" in text


def test_parse_paths_from_unit(tmp_path: Path) -> None:
    text = render_user_unit()
    home_p, work_p = parse_tomo_paths_from_unit(text, tmp_path)
    assert home_p == tmp_path / ".tomo"
    assert work_p == tmp_path / "tomo"


def test_parse_falls_back_when_env_missing(tmp_path: Path) -> None:
    home_p, work_p = parse_tomo_paths_from_unit("[Service]\n", tmp_path)
    assert home_p == tmp_path / ".tomo"
    assert work_p == tmp_path / "tomo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/cli/test_unit.py -v`  
Expected: FAIL import / missing symbols

- [ ] **Step 3: Write minimal implementation**

```python
# cli/unit.py
"""systemd user unit text for managed Tomo installs."""

from __future__ import annotations

import re
from pathlib import Path

from cli.paths import default_tomo_home, default_tomo_work

_UNIT = """\
[Unit]
Description=Tomo agent swarm
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/.local/share/tomo/app
ExecStart=%h/.local/share/tomo/app/.venv/bin/python -m app.main
Restart=on-failure
RestartSec=5
Environment=TOMO_HOME=%h/.tomo
Environment=TOMO_WORK=%h/tomo
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


def render_user_unit() -> str:
    return _UNIT


def _expand_systemd_path(value: str, home: Path) -> Path:
    v = value.strip().strip('"').strip("'")
    v = v.replace("%h", str(home))
    v = v.replace("$HOME", str(home))
    v = v.replace("${HOME}", str(home))
    return Path(v).expanduser()


def parse_tomo_paths_from_unit(text: str, home: Path) -> tuple[Path, Path]:
    home_path = default_tomo_home(home)
    work_path = default_tomo_work(home)
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("Environment="):
            continue
        payload = line.split("=", 1)[1]
        # Environment=KEY=VAL (VAL may contain =)
        if payload.startswith("TOMO_HOME="):
            home_path = _expand_systemd_path(payload[len("TOMO_HOME=") :], home)
        elif payload.startswith("TOMO_WORK="):
            work_path = _expand_systemd_path(payload[len("TOMO_WORK=") :], home)
    return home_path, work_path
```

Remove unused `re` import if you copy literally — do not leave unused imports.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/cli/test_unit.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cli/unit.py tests/unit/cli/test_unit.py
git commit -m "$(cat <<'EOF'
feat(cli): render systemd user unit with TOMO_HOME/TOMO_WORK

EOF
)"
```

---

### Task 3: Git sync helpers (TDD)

**Files:**
- Create: `cli/git_sync.py`
- Create: `tests/unit/cli/test_git_sync.py`

**Interfaces:**
- Produces:
  - `class GitSyncResult(dataclass)` with fields: `updated: bool`, `commits: int`, `head: str`, `stash_ref: str | None`, `used_hard_reset: bool`
  - `sync_to_origin(cwd: Path, branch: str = "main", *, restore_stash: bool = True, assume_yes: bool = False, input_fn: Callable[[str], str] | None = None, git_cmd: list[str] | None = None) -> GitSyncResult`
  - Raises `RuntimeError` on fetch failure (message mentions network/auth as appropriate)
- Consumes: subprocess only

**Test setup helper:** create two bare/local remotes in `tmp_path` so tests stay offline.

- [ ] **Step 1: Write failing tests** (structure)

```python
# tests/unit/cli/test_git_sync.py
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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
    # diverge local
    (local / "README").write_text("local\n", encoding="utf-8")
    _git(local, "add", "README")
    _git(local, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "local")
    # remote advances differently
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
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `uv run pytest tests/unit/cli/test_git_sync.py -v`  
Expected: FAIL missing module

- [ ] **Step 3: Implement `cli/git_sync.py`**

Implement Hermes-lite flow:

1. `git status --porcelain` → if dirty, `git stash push --include-untracked -m tomo-update-…`; record stash ref via `git rev-parse refs/stash`.
2. `git fetch origin` — on non-zero, raise `RuntimeError` with first stderr line; do not mutate further.
3. If not on `branch`, `git checkout branch` (create tracking if needed via `git checkout -B branch origin/branch` only when origin ref exists).
4. `commits = int(git rev-list HEAD..origin/{branch} --count)`.
5. If commits == 0: optionally restore stash; return `updated=False`.
6. Try `git pull --ff-only origin {branch}`; on failure `git reset --hard origin/{branch}` and set `used_hard_reset=True`.
7. Restore stash with `git stash apply` then `git stash drop` when `restore_stash` and stash exists; if `assume_yes` is False and `input_fn` provided, ask `Restore stashed local changes? [Y/n]`; on apply failure print guidance and leave stash.
8. Return short HEAD: `git rev-parse --short HEAD`.

Keep functions small; prefer private `_run(git_cmd, cwd, args) -> CompletedProcess`.

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run pytest tests/unit/cli/test_git_sync.py -v`  
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add cli/git_sync.py tests/unit/cli/test_git_sync.py
git commit -m "$(cat <<'EOF'
feat(cli): Hermes-style git sync for managed installs

EOF
)"
```

---

### Task 4: systemctl wrappers + uninstall logic

**Files:**
- Create: `cli/service.py`
- Create: `cli/uninstall_cmd.py`
- Create: `tests/unit/cli/test_uninstall.py`

**Interfaces:**
- Produces:
  - `systemctl_user(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]`
  - `service_action(action: str) -> int` where action ∈ `{status,start,stop,restart}`
  - `write_unit_file(home: Path | None = None) -> Path`
  - `uninstall(*, purge: bool, assume_yes: bool, home: Path | None = None, input_fn=..., run_systemctl: Callable | None = None) -> None`
- Consumes: `cli.paths.*`, `cli.unit.render_user_unit`, `parse_tomo_paths_from_unit`

**Safety for purge:** resolve home/work paths; if either resolved path is not under `Path(home).resolve()`, raise `RuntimeError` unless caller later adds an override (v1: no override — refuse).

- [ ] **Step 1: Failing uninstall tests**

```python
# tests/unit/cli/test_uninstall.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from cli.unit import render_user_unit
from cli.uninstall_cmd import uninstall


def test_uninstall_removes_code_keeps_data(tmp_path: Path) -> None:
    app = tmp_path / ".local/share/tomo/app"
    app.mkdir(parents=True)
    (app / "x").write_text("1", encoding="utf-8")
    unit = tmp_path / ".config/systemd/user/tomo.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(render_user_unit(), encoding="utf-8")
    link = tmp_path / ".local/bin/tomo"
    link.parent.mkdir(parents=True)
    link.symlink_to(app / ".venv/bin/tomo")
    data = tmp_path / ".tomo"
    data.mkdir()
    (data / "keep").write_text("1", encoding="utf-8")
    work = tmp_path / "tomo"
    work.mkdir()

    calls: list[tuple] = []

    def fake_systemctl(*args: str, check: bool = False):
        calls.append(args)
        return MagicMock(returncode=0)

    uninstall(
        purge=False,
        assume_yes=True,
        home=tmp_path,
        run_systemctl=fake_systemctl,
    )

    assert not app.exists()
    assert data.exists()
    assert work.exists()
    assert not unit.exists()
    assert not link.exists()
    assert ("stop", "tomo") in calls or any("stop" in a for a in calls)


def test_uninstall_purge_deletes_data(tmp_path: Path) -> None:
    app = tmp_path / ".local/share/tomo/app"
    app.mkdir(parents=True)
    unit = tmp_path / ".config/systemd/user/tomo.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(render_user_unit(), encoding="utf-8")
    data = tmp_path / ".tomo"
    data.mkdir()
    work = tmp_path / "tomo"
    work.mkdir()

    uninstall(
        purge=True,
        assume_yes=True,
        home=tmp_path,
        run_systemctl=lambda *a, check=False: MagicMock(returncode=0),
    )
    assert not data.exists()
    assert not work.exists()


def test_uninstall_purge_aborts_without_yes(tmp_path: Path) -> None:
    (tmp_path / ".local/share/tomo/app").mkdir(parents=True)
    unit = tmp_path / ".config/systemd/user/tomo.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(render_user_unit(), encoding="utf-8")
    (tmp_path / ".tomo").mkdir()

    try:
        uninstall(
            purge=True,
            assume_yes=False,
            home=tmp_path,
            input_fn=lambda _: "n",
            run_systemctl=lambda *a, check=False: MagicMock(returncode=0),
        )
    except SystemExit as e:
        assert e.code != 0
    else:
        # implementation may raise RuntimeError instead — either is fine if data kept
        pass
    assert (tmp_path / ".tomo").exists()
```

- [ ] **Step 2: Run — expect FAIL**

Run: `uv run pytest tests/unit/cli/test_uninstall.py -v`

- [ ] **Step 3: Implement `cli/service.py` and `cli/uninstall_cmd.py`**

`service.py` sketch:

```python
def systemctl_user(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def write_unit_file(home: Path | None = None) -> Path:
    path = unit_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_user_unit(), encoding="utf-8")
    return path


def service_action(action: str) -> int:
    if action not in {"status", "start", "stop", "restart"}:
        raise ValueError(action)
    # status should not use check=True; propagate returncode
    proc = systemctl_user(action, "tomo")
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    return int(proc.returncode)
```

`uninstall_cmd.py`: stop/disable (ignore errors) → remove unit → daemon-reload → remove symlink if it resolves under install_dir → `shutil.rmtree(install_dir)` → remove empty `…/share/tomo` → if purge, confirm, parse unit paths (read unit **before** deleting it — order matters: parse first), then rmtree home/work if under user home.

- [ ] **Step 4: Tests PASS**

Run: `uv run pytest tests/unit/cli/test_uninstall.py tests/unit/cli/test_unit.py -v`

- [ ] **Step 5: Commit**

```bash
git add cli/service.py cli/uninstall_cmd.py tests/unit/cli/test_uninstall.py
git commit -m "$(cat <<'EOF'
feat(cli): uninstall managed install with optional --purge

EOF
)"
```

---

### Task 5: `tomo update` command

**Files:**
- Create: `cli/update_cmd.py`
- Modify: tests if needed — `tests/unit/cli/test_update_cmd.py` (mock sync + systemctl)

**Interfaces:**
- Produces: `cmd_update(*, assume_yes: bool = False, home: Path | None = None) -> int`
- Consumes: `install_dir`, `read_tracked_branch`, `sync_to_origin`, `systemctl_user`

- [ ] **Step 1: Failing test**

```python
# tests/unit/cli/test_update_cmd.py
from pathlib import Path
from unittest.mock import patch

from cli.update_cmd import cmd_update


def test_update_missing_install(tmp_path: Path) -> None:
    code = cmd_update(assume_yes=True, home=tmp_path)
    assert code != 0


def test_update_runs_sync_and_restart(tmp_path: Path) -> None:
    app = tmp_path / ".local/share/tomo/app"
    app.mkdir(parents=True)
    (app / ".git").mkdir()
    with (
        patch("cli.update_cmd.sync_to_origin") as sync,
        patch("cli.update_cmd.systemctl_user") as sc,
        patch("cli.update_cmd._uv_sync", return_value=0) as uv,
    ):
        from cli.git_sync import GitSyncResult

        sync.return_value = GitSyncResult(
            updated=True, commits=2, head="abc1234", stash_ref=None, used_hard_reset=False
        )
        sc.return_value = type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        code = cmd_update(assume_yes=True, home=tmp_path)
    assert code == 0
    sync.assert_called_once()
    uv.assert_called_once()
    sc.assert_called()  # restart tomo
```

- [ ] **Step 2: Implement**

`cmd_update`:

1. `app = install_dir(home)`; if not `(app / ".git").is_dir()`: print hint to run install.sh; return 1.
2. `branch = read_tracked_branch(app)`.
3. `result = sync_to_origin(app, branch, assume_yes=assume_yes)`.
4. Run `uv sync` via `subprocess` with `cwd=app` (`shutil.which("uv")`); non-zero → return that code.
5. Try `systemctl --user restart tomo`; if fail (unit missing), print warning, still return 0 if sync+uv ok.
6. Print `Updated to {result.head}` or `Already up to date ({result.head})`.

- [ ] **Step 3: Tests pass + commit**

```bash
git add cli/update_cmd.py tests/unit/cli/test_update_cmd.py
git commit -m "$(cat <<'EOF'
feat(cli): add tomo update for managed git installs

EOF
)"
```

---

### Task 6: Wire argparse CLI entrypoint

**Files:**
- Modify: `cli/__main__.py`
- Modify: `cli/commands.py` (replace stub docstring with note that dispatch lives in `__main__`, or delete unused content)
- Optional smoke: `tests/unit/cli/test_main_help.py`

**Interfaces:**
- Produces: `main(argv: list[str] | None = None) -> int`  
  Subcommands: `update`, `uninstall`, `service`

- [ ] **Step 1: Failing help test**

```python
# tests/unit/cli/test_main_help.py
from cli.__main__ import main


def test_help_lists_subcommands() -> None:
    try:
        main(["--help"])
    except SystemExit as e:
        assert e.code == 0
```

Better: capture argparse via `main(["update", "--help"])` expecting SystemExit 0.

- [ ] **Step 2: Implement `__main__.py`**

```python
"""CLI entry: python -m cli or `tomo` after install."""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tomo", description="Tomo agent swarm CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("update", help="Update managed install from git")
    up.add_argument("-y", "--yes", action="store_true", help="Assume yes for prompts")

    un = sub.add_parser("uninstall", help="Remove managed install and user service")
    un.add_argument("--purge", action="store_true", help="Also delete TOMO_HOME and TOMO_WORK")
    un.add_argument("-y", "--yes", action="store_true", help="Skip confirmations")

    svc = sub.add_parser("service", help="Control systemd --user tomo.service")
    svc.add_argument(
        "action",
        choices=["status", "start", "stop", "restart"],
        help="systemctl --user action",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "update":
        from cli.update_cmd import cmd_update

        return cmd_update(assume_yes=bool(args.yes))
    if args.cmd == "uninstall":
        from cli.uninstall_cmd import uninstall

        uninstall(purge=bool(args.purge), assume_yes=bool(args.yes))
        return 0
    if args.cmd == "service":
        from cli.service import service_action

        return service_action(args.action)
    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

Note: `pyproject.toml` already has `tomo = "cli.__main__:main"` — ensure `main` returns int; console_scripts typically ignore return if not using `raise SystemExit`. Prefer:

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

For the entry point, wrap or document that hatch calls `main()` — if needed change to a void wrapper. Check hatchling behavior: returning int from console script is **not** used as exit code. Add:

```python
def main(argv: list[str] | None = None) -> None:
    raise SystemExit(_main(argv))
```

or keep `_run` / `main` returning int and use `raise SystemExit(main())` only in `__main__`, and set entry to a function that exits. Simplest fix: entry point function ends with `sys.exit(code)`.

- [ ] **Step 3: Run CLI unit tests + `uv run tomo --help` / `uv run python -m cli --help`**

Expected: help text shows update / uninstall / service

- [ ] **Step 4: Commit**

```bash
git add cli/__main__.py cli/commands.py tests/unit/cli/test_main_help.py
git commit -m "$(cat <<'EOF'
feat(cli): wire update, uninstall, and service subcommands

EOF
)"
```

---

### Task 7: `scripts/install.sh`

**Files:**
- Create: `scripts/install.sh` (executable)
- Modify: `scripts/README.md`

**Behavior:** match spec §4. Prefer calling Python helpers after `uv sync` when the tree exists (`uv run python -c 'from cli.unit import render_user_unit; …'`) **or** embed the same unit heredoc as `cli/unit.py` — keep them identical. **Required:** heredoc must include both `TOMO_HOME` and `TOMO_WORK` lines.

- [ ] **Step 1: Write `scripts/install.sh`**

Outline:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${TOMO_REPO_URL:-https://github.com/Alg0rix/tomo.git}"
BRANCH="main"
NO_START=0
INSTALL_DIR="${TOMO_INSTALL_DIR:-$HOME/.local/share/tomo/app}"

# parse --no-start / --branch
# ensure git; ensure uv (curl https://astral.sh/uv/install.sh)
# clone or sync (bash implementation of fetch + ff-only + reset --hard, with autostash)
# echo "$BRANCH" > "$INSTALL_DIR/.tomo-install-branch"
# (cd "$INSTALL_DIR" && uv sync)
# mkdir -p "$HOME/.config/systemd/user"
# write tomo.service from heredoc (must match cli/unit.py)
# systemctl --user daemon-reload
# systemctl --user enable tomo
# if [[ $NO_START -eq 0 ]]; then systemctl --user restart tomo || systemctl --user start tomo; fi
# mkdir -p "$HOME/.local/bin"
# ln -sfn "$INSTALL_DIR/.venv/bin/tomo" "$HOME/.local/bin/tomo"
# warn if ~/.local/bin not on PATH
# print success + URL http://127.0.0.1:8787
# mention linger for headless: loginctl enable-linger "$USER"
```

If `systemctl --user` fails (no user bus), still finish clone/sync/symlink and warn.

- [ ] **Step 2: Shellcheck mentally / `bash -n scripts/install.sh`**

Run: `bash -n scripts/install.sh`  
Expected: no output, exit 0

- [ ] **Step 3: Do NOT run install against the live user HOME in this workspace.** Optional: `HOME=$(mktemp -d) bash scripts/install.sh --no-start` only if network + git allowed; skip if constrained.

- [ ] **Step 4: Update `scripts/README.md`**

```markdown
# Development and release scripts.

- `install.sh` — bootstrap managed install under `~/.local/share/tomo/app` and systemd --user unit.
```

- [ ] **Step 5: Commit**

```bash
git add scripts/install.sh scripts/README.md
chmod +x scripts/install.sh
git commit -m "$(cat <<'EOF'
feat: add scripts/install.sh for systemd user bootstrap

EOF
)"
```

---

### Task 8: README Getting started

**Files:**
- Modify: `README.md` (Getting started section ~line 422)

- [ ] **Step 1: Insert section after the existing uv sync block**

```markdown
### Install as a user service (Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/Alg0rix/tomo/main/scripts/install.sh | bash
# or, from a checkout:
# bash scripts/install.sh
```

This clones into `~/.local/share/tomo/app`, runs `uv sync`, installs a **systemd user** unit (`tomo.service`) with `TOMO_HOME=~/.tomo` and `TOMO_WORK=~/tomo`, and symlinks `tomo` on `~/.local/bin`.

```bash
tomo update                 # git pull / reset + uv sync + restart
tomo service status
tomo uninstall              # remove service + code; keep data
tomo uninstall --purge      # also delete ~/.tomo and ~/tomo
```

Headless servers may need lingering so the user unit survives logout: `loginctl enable-linger $USER`.

Developer workflow (`uv sync` + `uv run python -m app.main`) is unchanged.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs: document git install and systemd user service

EOF
)"
```

---

### Task 9: Verification

- [ ] **Step 1: Run full CLI unit suite**

Run: `uv run pytest tests/unit/cli -v`  
Expected: all PASS

- [ ] **Step 2: Confirm unit template invariants**

Run: `uv run python -c "from cli.unit import render_user_unit; t=render_user_unit(); assert 'TOMO_HOME=%h/.tomo' in t and 'TOMO_WORK=%h/tomo' in t"`  
Expected: exit 0

- [ ] **Step 3: Confirm install.sh contains the same env lines**

Run: `grep -E 'TOMO_HOME|TOMO_WORK' scripts/install.sh`  
Expected: both present in the embedded unit

- [ ] **Step 4: Final commit only if stray fixes remain; otherwise done**

---

## Spec coverage checklist

| Spec item | Task |
|-----------|------|
| `install.sh` bootstrap | 7 |
| Path `~/.local/share/tomo/app` | 1, 7 |
| Unit sets `TOMO_HOME` + `TOMO_WORK` | 2, 7, 9 |
| Hermes git sync | 3, 5 |
| `tomo update` | 5, 6 |
| `tomo service *` | 4, 6 |
| `tomo uninstall` / `--purge` | 4, 6 |
| README | 8 |
| Unit tests git + uninstall | 3, 4 |
| No linger automation | 7 (docs only) |

## Self-review notes

- No `TOMO_WORKDIR` anywhere in plan.
- Uninstall parses unit **before** deleting it for purge path resolution.
- Live server not started by tests; install.sh verification uses `--no-start` / temp HOME only.
