#!/usr/bin/env bash
# Build tomo-connector and install a systemd --user unit.
# Usage: bash scripts/install-service.sh [--no-start]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NO_START=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-start) NO_START=1; shift ;;
    -h|--help)
      cat <<'EOF'
Build tomo-connector, install to ~/.local/bin, and enable systemd --user unit.

Options:
  --no-start   Write/enable unit but do not start
EOF
      exit 0
      ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 1 ;;
  esac
done

command -v go >/dev/null 2>&1 || { printf 'go is required\n' >&2; exit 1; }

cd "$ROOT"
printf '→ Building…\n'
make build

ARGS=(service install)
if [[ "$NO_START" -eq 1 ]]; then
  ARGS+=(--no-start)
fi
exec "$ROOT/tomo-connector" "${ARGS[@]}"
