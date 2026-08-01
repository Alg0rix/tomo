#!/usr/bin/env bash
# Convenience wrapper for Terminal-Bench 2.1 via Harbor.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
JOBS_DIR="${ROOT}/benchmarks/terminal_bench/jobs"
DATASET="terminal-bench/terminal-bench-2-1"
TOMO_AGENT_PATH="benchmarks.terminal_bench.tomo_agent:TomoHarborAgent"

mkdir -p "${JOBS_DIR}"

if ! command -v harbor >/dev/null 2>&1; then
  echo "harbor not found. Install with: uv tool install harbor" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running or not accessible." >&2
  exit 1
fi

usage() {
  cat <<'EOF'
Usage: run.sh <smoke|terminus|tomo|harbor> [harbor args...]

  smoke                 Oracle on first 5 tasks (no API key)
  terminus -m MODEL ... Terminus-2 (Harbor reference agent)
  tomo -m MODEL ...     Tomo Harbor scaffold (OpenAI tools → bash)
  harbor ...            Pass-through to: harbor run -d TB2.1 -o jobs ...

Common flags (forwarded to harbor):
  -m / --model MODEL    e.g. openai/gpt-4.1, anthropic/claude-sonnet-4-5
  -l / --n-tasks N      Limit number of tasks
  -i / --include-task-name NAME
  -k / --n-attempts N   Attempts per task (leaderboard often uses 5)
  -n / --n-concurrent N Parallel trials
EOF
}

cmd="${1:-}"
shift || true

case "${cmd}" in
  -h|--help|help|"")
    usage
    exit 0
    ;;
  smoke)
    exec harbor run \
      -d "${DATASET}" \
      -a oracle \
      -l 5 \
      -o "${JOBS_DIR}" \
      -n 2 \
      -y \
      "$@"
    ;;
  terminus)
    exec harbor run \
      -d "${DATASET}" \
      -a terminus-2 \
      -o "${JOBS_DIR}" \
      -y \
      "$@"
    ;;
  tomo)
    export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
    exec harbor run \
      -d "${DATASET}" \
      --agent-import-path "${TOMO_AGENT_PATH}" \
      -o "${JOBS_DIR}" \
      -y \
      "$@"
    ;;
  harbor)
    exec harbor run \
      -d "${DATASET}" \
      -o "${JOBS_DIR}" \
      -y \
      "$@"
    ;;
  *)
    echo "Unknown command: ${cmd}" >&2
    usage >&2
    exit 1
    ;;
esac
