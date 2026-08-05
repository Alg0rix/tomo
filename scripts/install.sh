#!/usr/bin/env bash
# Bootstrap a managed Tomo install + systemd --user unit.
# Usage: bash scripts/install.sh [--no-start] [--branch NAME]
set -euo pipefail

REPO_URL="${TOMO_REPO_URL:-https://github.com/Alg0rix/tomo.git}"
BRANCH="main"
NO_START=0
INSTALL_DIR="${TOMO_INSTALL_DIR:-$HOME/.local/share/tomo/app}"
UNIT_PATH="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/tomo.service"
BIN_LINK="${TOMO_BIN_LINK:-$HOME/.local/bin/tomo}"

log() { printf '%s\n' "$*"; }
warn() { printf '⚠ %s\n' "$*" >&2; }
die() { printf '✗ %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-start) NO_START=1; shift ;;
    --branch)
      [[ $# -ge 2 ]] || die "--branch requires a value"
      BRANCH="$2"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Install Tomo from git into ~/.local/share/tomo/app and enable a systemd user unit.

Options:
  --no-start       Write/enable unit but do not start
  --branch NAME    Clone/track branch (default: main)
EOF
      exit 0
      ;;
    *) die "Unknown option: $1" ;;
  esac
done

command -v git >/dev/null 2>&1 || die "git is required"

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  if [[ -x "$HOME/.local/bin/uv" ]]; then
    export PATH="$HOME/.local/bin:$PATH"
    return 0
  fi
  log "→ Installing uv..."
  curl -fsSL https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || die "uv install failed"
}

autostash_and_sync() {
  local dir="$1" branch="$2"
  local stash_ref=""
  if [[ -n "$(git -C "$dir" status --porcelain 2>/dev/null || true)" ]]; then
    local stamp
    stamp="$(date -u +%Y%m%d-%H%M%S)"
    log "→ Stashing local changes..."
    git -C "$dir" stash push --include-untracked -m "tomo-install-autostash-${stamp}" >/dev/null
    stash_ref="$(git -C "$dir" rev-parse --verify refs/stash)"
  fi
  git -C "$dir" fetch origin
  git -C "$dir" checkout -B "$branch" "origin/${branch}" 2>/dev/null \
    || git -C "$dir" checkout "$branch"
  if ! git -C "$dir" pull --ff-only "origin" "$branch"; then
    warn "Fast-forward not possible; resetting to origin/${branch}"
    git -C "$dir" reset --hard "origin/${branch}"
  fi
  if [[ -n "$stash_ref" ]]; then
    if git -C "$dir" stash apply "$stash_ref"; then
      git -C "$dir" stash drop >/dev/null 2>&1 || true
      warn "Restored local changes on top of updated tree — review git status"
    else
      warn "Could not restore stash; kept at $stash_ref"
    fi
  fi
}

write_unit() {
  mkdir -p "$(dirname "$UNIT_PATH")"
  cat >"$UNIT_PATH" <<'EOF'
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
EnvironmentFile=-%h/.tomo/.env
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF
}

# Persist bootstrap secrets via the same Python helper the app uses on start.
# Never overwrites existing keys. Prints a newly generated admin password once.
ensure_bootstrap_secrets() {
  local home_dir="${TOMO_HOME:-$HOME/.tomo}"
  local py="${INSTALL_DIR}/.venv/bin/python"
  mkdir -p "$home_dir"
  chmod 700 "$home_dir" 2>/dev/null || true

  if [[ ! -x "$py" ]]; then
    die "venv python missing at $py (uv sync failed?)"
  fi

  # Run from install tree so `app` is importable; TOMO_HOME selects the data root.
  local out
  if ! out="$(
    cd "$INSTALL_DIR" && TOMO_HOME="$home_dir" "$py" - <<'PY'
from app.core.bootstrap import ensure_bootstrap_secrets
r = ensure_bootstrap_secrets()
for n in r.notes:
    print(f"NOTE\t{n}")
if r.created_admin_password and r.admin_password:
    print(f"ADMIN_PASSWORD\t{r.admin_password}")
if r.created_session_secret:
    print("CREATED\tTOMO_SESSION_SECRET")
if r.created_secret_key:
    print("CREATED\t.secret_key")
print(f"ENV_FILE\t{r.env_path}")
PY
  )"; then
    die "failed to seed bootstrap secrets under $home_dir"
  fi

  local admin_pw="" env_file="$home_dir/.env"
  while IFS=$'\t' read -r kind msg; do
    case "$kind" in
      NOTE) log "→ $msg" ;;
      ADMIN_PASSWORD) admin_pw="$msg" ;;
      ENV_FILE) env_file="$msg" ;;
    esac
  done <<<"$out"

  if [[ -n "$admin_pw" ]]; then
    log ""
    log "╔══════════════════════════════════════════════════════════╗"
    log "║  Bootstrap admin login (save this — shown once)          ║"
    log "║  user:     admin                                         ║"
    log "║  password: $admin_pw"
    log "║  also in:  $env_file                                     ║"
    log "╚══════════════════════════════════════════════════════════╝"
    log ""
  fi
}

ensure_uv

mkdir -p "$(dirname "$INSTALL_DIR")"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  log "→ Updating existing install at $INSTALL_DIR"
  autostash_and_sync "$INSTALL_DIR" "$BRANCH"
else
  if [[ -e "$INSTALL_DIR" ]]; then
    die "Directory exists but is not a git repo: $INSTALL_DIR"
  fi
  log "→ Cloning $REPO_URL ($BRANCH) → $INSTALL_DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

printf '%s\n' "$BRANCH" >"$INSTALL_DIR/.tomo-install-branch"

# A stale uv.lock from a different Python version can break sync. Regenerate it.
rm -f "$INSTALL_DIR/uv.lock"

log "→ uv sync (Python 3.13)"
(cd "$INSTALL_DIR" && uv sync --python 3.13)

ensure_bootstrap_secrets

write_unit
log "→ Wrote $UNIT_PATH"

SYSTEMD_OK=1
if systemctl --user daemon-reload 2>/dev/null \
  && systemctl --user enable tomo 2>/dev/null; then
  if [[ "$NO_START" -eq 0 ]]; then
    systemctl --user restart tomo 2>/dev/null \
      || systemctl --user start tomo 2>/dev/null \
      || warn "Could not start tomo.service"
  fi
else
  SYSTEMD_OK=0
  warn "systemctl --user unavailable; skipped enable/start"
fi

mkdir -p "$(dirname "$BIN_LINK")"
ln -sfn "$INSTALL_DIR/.venv/bin/tomo" "$BIN_LINK"
log "→ Symlinked $BIN_LINK → $INSTALL_DIR/.venv/bin/tomo"

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) warn "~/.local/bin is not on PATH — add it to use the tomo command" ;;
esac

log ""
log "✓ Tomo installed"
log "  Code:  $INSTALL_DIR"
log "  Data:  \$TOMO_HOME (~/.tomo)  work: \$TOMO_WORK (~/tomo)"
log "  Secrets: \$TOMO_HOME/.env (TOMO_SESSION_SECRET, TOMO_ADMIN_PASSWORD) + .secret_key"
log "  UI:    http://127.0.0.1:8787"
log "  Login: admin + TOMO_ADMIN_PASSWORD from \$TOMO_HOME/.env (printed above if newly generated)"
if [[ "$SYSTEMD_OK" -eq 1 ]]; then
  log "  Unit:  tomo.service (systemd --user)"
fi
log ""
log "Later: tomo update | tomo service status | tomo uninstall"
log "Headless servers may need: loginctl enable-linger \"\$USER\""
