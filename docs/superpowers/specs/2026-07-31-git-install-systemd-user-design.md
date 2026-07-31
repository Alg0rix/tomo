# Git install / update + systemd user service

**Date:** 2026-07-31  
**Status:** Approved (user: Hermes-lite · XDG app path · TOMO_HOME/TOMO_WORK in unit · uninstall + `--purge`)  
**Roles:** Cursor plans/implements  

---

## 1. Goal

Ship a first-run **install from git** path and a durable **update** path, and run Tomo as a **systemd user** service. Match Hermes UX lightly: `scripts/install.sh` for bootstrap, then `tomo update` / `tomo uninstall` from the CLI.

---

## 2. Decisions (locked)

| Topic | Choice |
|-------|--------|
| Invocation | Bootstrap `scripts/install.sh`; post-install CLI (`tomo update`, `tomo uninstall`, `tomo service …`) |
| Code install path | Fixed: `~/.local/share/tomo/app` |
| Data | Unchanged: `$TOMO_HOME` default `~/.tomo`; `$TOMO_WORK` default `~/tomo` |
| Update git strategy | Hermes-style: autostash → `fetch` → `pull --ff-only origin main` → on diverge `reset --hard origin/main` → restore stash (prompt unless `-y`) |
| Systemd scope | **User** unit only (`systemctl --user`) |
| Unit env | Explicitly set `TOMO_HOME` and `TOMO_WORK` (do not rely on process defaults alone) |
| Uninstall | Default: service + symlink + code tree; `--purge` also removes `$TOMO_HOME` and `$TOMO_WORK` |
| Out of scope (v1) | `loginctl enable-linger`, launchd, Windows, fork/upstream sync, PyPI install method |

There is **no** `TOMO_WORKDIR` env. Agent tool cwd uses **`TOMO_WORK`**. Systemd `WorkingDirectory` is the **code** tree, not `$TOMO_WORK`.

---

## 3. Path layout

| Path | Role |
|------|------|
| `~/.local/share/tomo/app` | Managed git checkout + `.venv` (code) |
| `~/.local/bin/tomo` | Symlink to install entrypoint (`…/app/.venv/bin/tomo`) |
| `~/.config/systemd/user/tomo.service` | User unit |
| `$TOMO_HOME` (`~/.tomo`) | Config, DB, secrets, library |
| `$TOMO_WORK` (`~/tomo`) | Per-agent tool workspaces (`$TOMO_WORK/<agent_id>`) |

Dev checkouts (e.g. this workspace) are unrelated; install/update always target the managed path unless explicitly documented otherwise. Running `tomo update` from a non-managed tree should detect the managed install and operate on it (or error clearly if none exists).

---

## 4. Bootstrap — `scripts/install.sh`

Idempotent. Typical one-liner later:  
`curl -fsSL https://raw.githubusercontent.com/Alg0rix/tomo/main/scripts/install.sh | bash`

### Steps

1. Require Linux + `systemctl --user` available.
2. Ensure `git` and `uv` on PATH (install `uv` via official installer if missing).
3. Create parent dirs; if `~/.local/share/tomo/app/.git` missing →  
   `git clone --branch main https://github.com/Alg0rix/tomo.git` into that path.  
   If present → run the same sync logic as update (fetch / ff-only / hard-reset fallback), without requiring the CLI yet.
4. `cd` install dir → `uv sync` (respect `uv.lock`).
5. Write `~/.config/systemd/user/tomo.service` (see §5).
6. `systemctl --user daemon-reload && systemctl --user enable --now tomo`.
7. Symlink `~/.local/bin/tomo` → `$INSTALL_DIR/.venv/bin/tomo` (create `~/.local/bin` if needed; warn if not on PATH).
8. Print URL (`http://127.0.0.1:8787` by default) and next steps.

### Flags (minimal)

| Flag | Effect |
|------|--------|
| `--no-start` | Write/enable unit but do not start |
| `--branch NAME` | Clone/track branch (default `main`) |

Do **not** delete `$TOMO_HOME` / `$TOMO_WORK` on reinstall.

---

## 5. Systemd user unit

Unit name: `tomo.service`.

```ini
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
# Optional later: EnvironmentFile=-%h/.tomo/.env
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

**Required:** `TOMO_HOME` and `TOMO_WORK` must appear in the unit (or an `EnvironmentFile` that sets both). Forgetting them is a bug — agent sandboxes and DB location would silently depend on whatever the user session inherited.

`TOMO_HOST` / `TOMO_PORT` stay at app defaults unless the user adds them to `$TOMO_HOME/.env` or extends the unit later. No linger automation in v1; document that headless servers may need `loginctl enable-linger $USER`.

---

## 6. CLI

Extend the stub `tomo` entry (`cli/`) with argparse (or equivalent) subcommands. Shared install-path constants live in something like `cli/paths.py` / `cli/service.py` so install.sh can stay thin and Python owns update/uninstall after first install.

### `tomo update`

1. Resolve managed install dir; fail if missing (hint: run `install.sh`).
2. Autostash if dirty (include untracked).
3. `git fetch origin`; ensure on `main` (or tracked branch from install metadata if we store `--branch`).
4. If already up to date → restore stash if any → exit 0.
5. `git pull --ff-only origin <branch>`; on failure → `git reset --hard origin/<branch>`.
6. Restore stash (prompt unless `--yes` / `-y`).
7. `uv sync` in install dir.
8. `systemctl --user restart tomo` if the unit is enabled/loaded; warn if not installed as a service.
9. Print new HEAD short hash.

### `tomo service {status,start,stop,restart}`

Thin wrappers around `systemctl --user <cmd> tomo` (and `daemon-reload` only when needed for install, not every restart).

### `tomo uninstall`

Default:

1. `systemctl --user stop tomo` / `disable tomo` (ignore if absent).
2. Remove `~/.config/systemd/user/tomo.service`; `daemon-reload`.
3. Remove `~/.local/bin/tomo` if it points at the managed install.
4. Delete `~/.local/share/tomo/app` (and empty parent `~/.local/share/tomo` if empty).

`--purge`:

5. After confirm (or `-y`), delete `$TOMO_HOME` and `$TOMO_WORK` as resolved from the unit env if present, else defaults `~/.tomo` and `~/tomo`. Refuse to purge paths outside the user’s home without an explicit override (safety).

---

## 7. Error handling

| Case | Behavior |
|------|----------|
| No network on fetch | Exit non-zero; clear message; leave tree untouched after failed fetch |
| Not a git install dir | Tell user to re-run `install.sh` |
| `systemctl --user` unavailable | Install may still clone + sync; skip/warn on unit steps; update still syncs code |
| Stash restore conflicts | Leave stash; print `git stash apply` ref; update still considered succeeded |
| Uninstall without purge | Never touch data dirs |
| Purge without confirm and no `-y` | Abort |

---

## 8. Docs / README

Add a short **Install as a user service** section to README Getting started:

```bash
curl -fsSL https://raw.githubusercontent.com/Alg0rix/tomo/main/scripts/install.sh | bash
# later
tomo update
tomo uninstall          # keep ~/.tomo and ~/tomo
tomo uninstall --purge  # also delete data + work trees
```

Keep the existing `uv sync` / `uv run python -m app.main` path for developers.

---

## 9. Tests

- Unit tests for git sync decision helpers (ff-only vs hard reset; stash skipped when clean) with temp git repos.
- Unit tests for uninstall path selection (default vs `--purge`) without actually calling systemctl (mock subprocess).
- Optional: dry-run flag later; not required for v1.

---

## 10. Non-goals

- Embedding secrets in the unit file.
- Migrating an existing git worktree into the managed path automatically.
- Changing `$TOMO_HOME` / `$TOMO_WORK` semantics or renaming to `TOMO_WORKDIR`.
- Root/system-wide systemd unit.
