# Tomo Connector — Design (v1)

**Date:** 2026-07-26  
**Status:** Implemented (Go connector + FastAPI hub)

## Goal

Make `kind=tunnel` workplaces real: a lightweight agent on a remote device
opens an **outbound WebSocket** to Tomo, pairs once, stays connected, and runs
the same tools (`bash`, `read_file`, `write_file`) as local workplaces —
behind NAT without inbound ports or SSH.

## Components

| Piece | Role |
|-------|------|
| Tomo server (`app/workplaces/hub.py`, `app/api/connector.py`) | Pairing, token storage, live session registry, RPC dispatch |
| Go binary (`connector/`) | `tomo-connector pair\|run\|status\|logout` |
| Tool backends | Route to hub when agent workplace is tunnel + online |

## Protocol (v1)

### Pair (HTTP, no admin session)

`POST /api/connector/pair`

```json
{"pairing_code":"X7KQ2M","device_name":"pi","platform":"linux","version":"0.2.0"}
→ {"ok":true,"connector_token":"…","workplace_id":"wp_pi","workplace_name":"…"}
```

Pairing codes: 6 chars, no ambiguous `0/O/1/I`, TTL **15 min**. After pair, DB status is **offline** until WS is live.

### Connect (WebSocket)

Path: **`/api/connector/ws`**

Preferred auth: handshake headers

| Header | Purpose |
|--------|---------|
| `Authorization: Bearer <token>` | connector token |
| `X-Device-Name` | hostname |
| `X-Platform` | GOOS |
| `X-Tomo-Connector-Version` | e.g. `0.2.0` |
| `X-Tomo-Caps: idempotent-replay` | allow server re-send after reconnect |

JSON fallback still supports `pair` / `hello` message types.

### Keep-alive / RPC

```json
→ {"v":1,"type":"ping"}  ← {"v":1,"type":"pong"}
← {"v":1,"type":"rpc_request","id":"…","method":"bash","params":{"command":"pwd"}}
→ {"v":1,"type":"rpc_response","id":"…","ok":true,"result":"/home/…"}
```

### Supported operations

| Method | Description |
|--------|-------------|
| `exec_bash` | Bash script → `{stdout, stderr, exit_code, execution_time}` |
| `exec_python` | Python snippet → same shape |
| `read_file` | Text file → `{content, size, path}` |
| `write_file` | Write/append → `{ok, path}` |
| `read_file_b64` / `write_file_b64` | Chunked binary (portal-ready) |
| `bash` | Alias of `exec_bash` |

Agent tools: `bash` → `exec_bash`, `runpy` → `exec_python`, `read_file` / `write_file` unchanged names.

**Exactly-once:** connector caches by request id (~5 min); hub keeps pending RPCs across reconnect when `replay_ok` (cap or version ≥ 0.2.0), grace **90s**.

## Status model

| Status | Meaning |
|--------|---------|
| `pairing` | Code issued; waiting for connector |
| `connected` | Live WebSocket on hub only |
| `offline` | Paired or not; no live socket |
| `later` | Legacy; unused for new tunnels |

API cannot set tunnel status to `connected` without the hub.

## Data

On `workplaces`:

- `pairing_code`, `pairing_expires_at` (TTL ~30 min, single active code)
- `connector_token` — Fernet ciphertext (`enc:v1:`)
- `connector_last_seen_at`, `connector_version`, `connector_hostname`

## Threat model (short)

| Threat | Mitigation |
|--------|------------|
| Guess pairing code | Short TTL; rate-limit pair/hello; large alphabet |
| Token theft at rest (server) | Fernet via `$TOMO_HOME/.secret_key` |
| Token theft on device | State file mode `0600` under `~/.tomo-connector` |
| Path escape on device | Jail under work root; reject `..` / absolute paths |
| Long bash | Timeouts on connector + RPC wait |
| Fake “connected” UI | Status only from hub live map |
| MITM | Prefer HTTPS/WSS in production; document TLS |

## Non-goals (v1)

- Fyne desktop GUI  
- Portal file bridge product  
- Replacing SSH workplaces  
- Multi-connector load-balancing per workplace (last session wins)

## Operator notes

- Do not start Tomo from the connector; human runs the server.
- Production: terminate TLS in front of uvicorn; allowlist server URL on the device if policy requires.
