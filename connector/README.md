# Tomo Connector (Go)

Lightweight agent that opens an **outbound WebSocket** to the Tomo server and
runs the same tool surface for `kind=tunnel` workplaces — no inbound ports or SSH.

Layout:

```
connector/
├── cmd/tomo-connector/   # CLI entrypoint
├── internal/
│   ├── version/          # version string
│   ├── state/            # ~/.tomo-connector state
│   ├── pair/             # HTTP pair
│   ├── ws/               # WebSocket client + RPC loop
│   └── executor/         # exec_bash, files, process jobs
├── Makefile
└── README.md
```

## Install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/Alg0rix/tomo/main/scripts/install-connector.sh | bash
```

Downloads the matching binary from GitHub Releases into `~/.local/bin`. Re-run to update (overwrites the binary and restarts the user service if enabled). Pin a tag with `TOMO_CONNECTOR_VERSION=v0.1.3`.

## Build

```bash
cd connector
go mod tidy
make build
# or: go build -o tomo-connector ./cmd/tomo-connector

# Cross-compile linux/darwin/windows → dist/ (same targets as CI)
make dist
```

CI (`.github/workflows/ci.yml`) builds these binaries on every push/PR and attaches them to `v*` GitHub Releases with the Python wheel.

## Pair & run

```bash
tomo-connector pair --code X7KQ2M --server http://coordinator:8787
tomo-connector run
tomo-connector status
tomo-connector logout
```

Optional: `TOMO_CONNECTOR_PAIR_AND_RUN=1` makes `pair` also start `run`.

State: `~/.tomo-connector/` (or `$TOMO_CONNECTOR_HOME`).  
Jail: `$TOMO_CONNECTOR_ROOT` or `$TOMO_CONNECTOR_HOME/work`.

## Private-input broker in tunnel chats

With an updated Tomo server, this connector advertises `secret-broker` on
HTTPS/WSS pairings (loopback HTTP is permitted for local testing). Install the
matching `tomo` Python CLI on this host as well; the Go connector alone does not
install it. Existing foreground web-chat bash commands work unchanged:

```bash
tomo secret request app --form '{"fields":[{"name":"API_KEY"}]}'
tomo secret list
tomo secret apply app --file .env --format compose
tomo connection request api --url https://api.example --auth bearer
tomo http --connection api /status
tomo secret revoke app
```

Input stays in the browser form and encrypted backend store. A loopback-only
HTTP bridge forwards CLI broker metadata to the paired server's HTTPS API.
Private consumer inputs use internal authenticated connector RPC, not shell
commands or environment variables. HTTP executes on this host, so its intranet
network/DNS is used. Files are rendered by the same backend logic and atomically
written inside `TOMO_CONNECTOR_ROOT`, with a content check against concurrent
edits. Results/errors contain only metadata; normal filesystem access remains
unchanged. Add generated secret files to the application's `.gitignore`.

The server/proxy must permit broker HTTPS paths as well as the connector WS
path. No public listener is added on this host. Ephemeral broker access is bound
to the web-chat session and workplace and ends when its bash call exits/stops.
Old/offline/public-plaintext connectors, SSH, background jobs and standalone
terminals do not get this capability. Do not print/copy broker tokens.

Private read/HTTP replies stay only in bounded memory for same-process replay;
only the intent fingerprint is journaled. After restart, those intents return
uncertain status rather than rerunning an HTTP mutation. Verify effects before
retrying; never inspect/print secret contents to recover. Revoking a bundle does
not remove files already materialized or credentials already used by an app.

## systemd service

Keep the connector online across reboots/logouts (Linux). Install mode depends
on the installing user:

| Who runs install | Unit type | Binary default | Unit path |
|------------------|-----------|----------------|-----------|
| Normal user | `systemd --user` | `~/.local/bin/tomo-connector` | `~/.config/systemd/user/` |
| **root** | **system** (`multi-user.target`) | `/usr/local/bin/tomo-connector` | `/etc/systemd/system/` |

Root cannot reliably use `systemctl --user` (no user session / D-Bus for uid 0),
so root installs a **system** unit instead.

```bash
# Pair first (once), then:
make build
./tomo-connector service install
# or: bash scripts/install-service.sh
# or: make install-service

tomo-connector service status
# non-root only:
loginctl enable-linger $USER   # optional: survive logout
```

| Command | Effect |
|---------|--------|
| `service install [--no-start]` | Copy binary, write unit (user or system), enable (+ start) |
| `service uninstall` | Disable/stop and remove the unit (keeps binary + pairing state) |
| `service start\|stop\|restart\|status` | `systemctl [--user] … tomo-connector` |

Unit templates: [`deploy/tomo-connector.service`](deploy/tomo-connector.service) (user),
[`deploy/tomo-connector.system.service`](deploy/tomo-connector.system.service) (root).  
Override binary path with `TOMO_CONNECTOR_BIN` if needed.

## Protocol (v1)

### Pair — `POST /api/connector/pair`

```json
{"pairing_code":"X7KQ2M","device_name":"pi","platform":"linux","version":"0.2.0"}
→ {"ok":true,"connector_token":"…","workplace_id":"wp_pi"}
```

### Connect — `WS /api/connector/ws`

Headers: `Authorization: Bearer <token>`, `X-Device-Name`, `X-Platform`,
`X-Tomo-Connector-Version`, `X-Tomo-Caps: idempotent-replay,exec-stream`.

| Method | Params | Result |
|--------|--------|--------|
| `exec_bash` | `{script, timeout, env, cwd, stream?}` | `{stdout, stderr, exit_code, …}` |
| `exec_python` | `{code, …}` | same |
| `read_file` / `write_file` | path/content | structured |
| `str_replace` / `patch` / `delete_file` / `search_files` | … | structured |
| `process_start` / `list` / `status` / `kill` | jobs | job records |
| `read_file_b64` / `write_file_b64` | binary chunks | portal-ready |

Live output: when the server sends `exec_bash` with `"stream": true` (only to
connectors advertising `exec-stream`), the connector emits
`{"type":"rpc_progress","id":…,"result":{"data":"…"}}` every ~100 ms while the
command runs, then the normal `rpc_response`. Journal replays send no progress.

Exactly-once: client caches RPC results by request `id` (~5 min).
