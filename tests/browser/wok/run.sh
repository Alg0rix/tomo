#!/usr/bin/env bash
# Run the wok browser flows against Tomo's real static JS.
#   tests/browser/wok/run.sh                 # every *.wok.yaml
#   tests/browser/wok/run.sh queue_park      # selected flows
# WOK_BROWSER=/usr/bin/google-chrome selects the browser (needed where the
# bundled Chromium has no usable sandbox).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../../.." && pwd)
profile=${WOK_PROFILE:-tomo-browser-tests}
artifacts=${WOK_ARTIFACTS:-${TMPDIR:-/tmp}/tomo-wok-artifacts}

# Serve only what fixtures load, never the whole checkout.
site=$(mktemp -d)
mkdir -p "$site/app" "$site/tests/browser/wok"
ln -s "$root/app/static" "$site/app/static"
ln -s "$here/fixtures" "$site/tests/browser/wok/fixtures"
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
python3 -m http.server "$port" --bind 127.0.0.1 --directory "$site" >/dev/null 2>&1 &
server=$!
cleanup() {
  kill "$server" 2>/dev/null || true
  wok --profile "$profile" stop >/dev/null 2>&1 || true
  wok --profile "$profile" delete-data >/dev/null 2>&1 || true
  rm -rf "$site"
}
trap cleanup EXIT
for _ in $(seq 50); do curl -fs -o /dev/null "http://127.0.0.1:$port/" && break; sleep 0.1; done

wok --profile "$profile" delete-data >/dev/null 2>&1 || true
start=(--profile "$profile" start)
if [ -n "${WOK_BROWSER:-}" ]; then start+=(--browser "$WOK_BROWSER"); fi
wok "${start[@]}" >/dev/null

# Failed step name, its error and the failure screenshot.
summarize='
import json, sys
raw = sys.stdin.read()
try:
    report = json.loads(raw)
except ValueError:
    print(raw.strip() or "no report"); sys.exit()
for step in report.get("steps", []):
    if not step.get("ok"):
        print("step %s %s: %s" % (step.get("index"), step.get("name") or step.get("action"), step.get("error", "")))
if report.get("screenshot"):
    print("screenshot: " + report["screenshot"])
'

if [ $# -gt 0 ]; then flows=("$@"); else flows=("$here"/*.wok.yaml); fi
failed=0
for flow in "${flows[@]}"; do
  case $flow in */*) ;; *) flow="$here/$flow.wok.yaml" ;; esac
  name=$(basename "$flow" .wok.yaml)
  if report=$(wok run "$flow" --profile "$profile" --artifacts "$artifacts" \
      --var BASE_URL="http://127.0.0.1:$port/tests/browser/wok/fixtures" 2>/dev/null); then
    echo "ok    $name"
  else
    echo "FAIL  $name"
    printf '%s' "$report" | python3 -c "$summarize" | sed 's/^/      /'
    failed=$((failed + 1))
  fi
done
[ "$failed" -eq 0 ] || { echo "$failed flow(s) failed"; exit 1; }
