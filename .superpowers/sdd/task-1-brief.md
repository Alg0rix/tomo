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

