#!/usr/bin/env bash
# Install or update a prebuilt tomo-connector from GitHub Releases into ~/.local/bin.
# Re-running replaces the binary (and restarts the user service if enabled).
# Usage: curl -fsSL …/scripts/install-connector.sh | bash
# Env: TOMO_CONNECTOR_VERSION=v0.1.3 (default: latest)
set -euo pipefail

REPO="${TOMO_CONNECTOR_REPO:-Alg0rix/tomo}"
BIN_DIR="${TOMO_CONNECTOR_BIN_DIR:-$HOME/.local/bin}"
DEST="${BIN_DIR}/tomo-connector"
VERSION="${TOMO_CONNECTOR_VERSION:-latest}"
UNIT="tomo-connector.service"

die() { printf '✗ %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || die "curl is required"

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "$OS" in
  linux|darwin) ;;
  mingw*|msys*|cygwin*) die "use the Windows .exe from GitHub Releases" ;;
  *) die "unsupported OS: $OS" ;;
esac

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) ARCH=amd64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) die "unsupported arch: $ARCH" ;;
esac

ASSET="tomo-connector-${OS}-${ARCH}"
if [[ "$VERSION" == "latest" ]]; then
  URL="https://github.com/${REPO}/releases/latest/download/${ASSET}"
else
  URL="https://github.com/${REPO}/releases/download/${VERSION}/${ASSET}"
fi

UPDATING=0
if [[ -x "$DEST" ]]; then
  UPDATING=1
  echo "→ Updating existing install at ${DEST}"
else
  echo "→ Installing to ${DEST}"
fi

mkdir -p "$BIN_DIR"
TMP="$(mktemp "${TMPDIR:-/tmp}/tomo-connector.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

echo "→ Downloading ${ASSET} (${VERSION})…"
curl -fsSL -o "$TMP" "$URL" || die "download failed: $URL"
chmod +x "$TMP"
# Atomic replace so a running reader still sees the old inode until restart
mv -f "$TMP" "$DEST"
trap - EXIT

if [[ "$UPDATING" -eq 1 ]]; then
  echo "✓ Updated ${DEST}"
else
  echo "✓ Installed ${DEST}"
fi

case ":$PATH:" in
  *":${BIN_DIR}:"*) ;;
  *) echo "  Add to PATH: export PATH=\"${BIN_DIR}:\$PATH\"" ;;
esac

# Pick up the new binary if the user unit is already installed.
if command -v systemctl >/dev/null 2>&1; then
  if systemctl --user is-enabled "$UNIT" >/dev/null 2>&1 \
    || systemctl --user is-active "$UNIT" >/dev/null 2>&1; then
    echo "→ Restarting ${UNIT}…"
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user restart "$UNIT" || die "binary updated but service restart failed"
    echo "✓ ${UNIT} restarted"
  fi
fi

if [[ "$UPDATING" -eq 0 ]]; then
  echo
  echo "Next:"
  echo "  tomo-connector pair --code <CODE> --server https://your-coordinator.example.com"
  echo "  tomo-connector service install   # systemd --user (Linux)"
fi
